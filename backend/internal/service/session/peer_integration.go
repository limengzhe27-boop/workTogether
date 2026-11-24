package session

import (
	"context"
	"encoding/hex"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

type PeerIntegrationRequest struct {
	Epoch int64  `json:"epoch"`
	Base  string `json:"base"`
	Name  string `json:"name"`
}
type PeerIntegrationResult struct {
	Branch string   `json:"branch"`
	Commit string   `json:"commit"`
	Tasks  []string `json:"tasks"`
}

func gitPeer(ctx context.Context, repo string, args ...string) (string, error) {
	argv := append([]string{"-C", repo}, args...)
	cmd := exec.CommandContext(ctx, "git", argv...)
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(string(out)))
	}
	return strings.TrimSpace(string(out)), nil
}
func validPeerSHA(s string) bool {
	if len(s) != 40 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil
}

// Only immutable objects and a new candidate ref are written. No checkout/index/main mutation.
func buildPeerCandidate(ctx context.Context, repo, base, name string, commits []string) (PeerIntegrationResult, error) {
	result := PeerIntegrationResult{Branch: "codex/integration-" + name}
	if !validPeerSHA(base) || !regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9-]{0,60}$`).MatchString(name) || len(commits) == 0 {
		return result, fmt.Errorf("invalid candidate request")
	}
	if _, err := gitPeer(ctx, repo, "cat-file", "-e", base+"^{commit}"); err != nil {
		return result, err
	}
	current := base
	for _, candidate := range commits {
		if !validPeerSHA(candidate) {
			return result, fmt.Errorf("invalid candidate commit")
		}
		if _, err := gitPeer(ctx, repo, "merge-base", "--is-ancestor", base, candidate); err != nil {
			return result, fmt.Errorf("candidate does not descend from fixed base: %w", err)
		}
		if candidate == current {
			continue
		}
		tree, err := gitPeer(ctx, repo, "merge-tree", "--write-tree", current, candidate)
		if err != nil {
			return result, fmt.Errorf("integration conflict or unsupported merge; no branch published: %w", err)
		}
		if !validPeerSHA(tree) {
			return result, fmt.Errorf("unexpected merge-tree output")
		}
		current, err = gitPeer(ctx, repo, "commit-tree", tree, "-p", current, "-p", candidate, "-m", "Integrate independently reviewed peer task candidates")
		if err != nil {
			return result, err
		}
	}
	if _, err := gitPeer(ctx, repo, "update-ref", "refs/heads/"+result.Branch, current, strings.Repeat("0", 40)); err != nil {
		return result, fmt.Errorf("candidate branch already exists or publication failed: %w", err)
	}
	result.Commit = current
	return result, nil
}
func (s *Service) IntegratePeerRoom(ctx context.Context, id string, in PeerIntegrationRequest) (PeerIntegrationResult, error) {
	var result PeerIntegrationResult
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	room, err := s.GetPeerRoom(ctx, id)
	if err != nil {
		return result, err
	}
	if room.Paused || room.Epoch != in.Epoch {
		return result, fmt.Errorf("paused room or stale epoch")
	}
	tasks, err := s.ListPeerTasks(ctx, id)
	if err != nil || len(tasks) == 0 {
		return result, fmt.Errorf("room has no integrable tasks: %v", err)
	}
	project, ok, err := s.store.GetProject(ctx, room.ProjectID)
	if err != nil || !ok {
		return result, fmt.Errorf("project unavailable")
	}
	common, err := gitPeer(ctx, project.Path, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil {
		return result, err
	}
	commits := []string{}
	ids := []string{}
	for _, task := range tasks {
		if task.State != "accepted" || task.Owner == "" || task.Reviewer == "" || task.Owner == task.Reviewer || !validPeerSHA(task.Commit) {
			return result, fmt.Errorf("task %s requires independent acceptance of a fixed commit", task.ID)
		}
		owner, ok, err := s.store.GetSession(ctx, domain.SessionID(task.Owner))
		if err != nil || !ok || string(owner.ProjectID) != room.ProjectID {
			return result, fmt.Errorf("task owner unavailable")
		}
		ownerCommon, err := gitPeer(ctx, owner.Metadata.WorkspacePath, "rev-parse", "--path-format=absolute", "--git-common-dir")
		if err != nil || ownerCommon != common {
			return result, fmt.Errorf("task workspace belongs to another repository")
		}
		if _, err = gitPeer(ctx, owner.Metadata.WorkspacePath, "merge-base", "--is-ancestor", task.Commit, "HEAD"); err != nil {
			return result, fmt.Errorf("reviewed commit is absent from owner history")
		}
		commits = append(commits, task.Commit)
		ids = append(ids, task.ID)
	}
	recorder, ok := s.store.(interface {
		RecordPeerCandidate(context.Context, string, domain.PeerCandidate) error
	})
	if !ok {
		return result, fmt.Errorf("候选记录不可用")
	}
	result, err = buildPeerCandidate(ctx, project.Path, in.Base, in.Name, commits)
	if err != nil {
		return result, err
	}
	result.Tasks = ids
	// Once Git publishes the ref, preserve its evidence even if the UI closes.
	recordCtx, finishRecord := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer finishRecord()
	if err := recorder.RecordPeerCandidate(recordCtx, id, domain.PeerCandidate{Commit: result.Commit, Branch: result.Branch, Tasks: ids}); err != nil {
		return result, fmt.Errorf("候选分支 %s 已生成（%s），但未能保存记录: %w", result.Branch, result.Commit, err)
	}
	return result, nil
}

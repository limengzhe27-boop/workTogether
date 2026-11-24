package session

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestPeerIntegrationPreservesMainAndRejectsConflicts(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()
	git := func(input string, args ...string) string {
		t.Helper()
		cmd := exec.Command("git", append([]string{"-C", repo}, args...)...)
		cmd.Stdin = strings.NewReader(input)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	git("", "init", "-q")
	git("", "config", "user.name", "Fixture")
	git("", "config", "user.email", "fixture@example.invalid")
	empty := git("", "mktree")
	base := git("", "commit-tree", empty, "-m", "base")
	git("", "update-ref", "refs/heads/main", base)
	git("", "symbolic-ref", "HEAD", "refs/heads/main")
	makeCommit := func(file, content string) string {
		blob := git(content, "hash-object", "-w", "--stdin")
		tree := git("100644 blob "+blob+"\t"+file+"\n", "mktree")
		return git("", "commit-tree", tree, "-p", base, "-m", file)
	}
	a := makeCommit("a.txt", "alpha")
	b := makeCommit("b.txt", "beta")
	conflict := makeCommit("a.txt", "different")
	if err := os.WriteFile(filepath.Join(repo, "user-draft.txt"), []byte("keep my draft"), 0600); err != nil {
		t.Fatal(err)
	}
	before := git("", "status", "--porcelain")
	result, err := buildPeerCandidate(ctx, repo, base, "fixture", []string{a, b})
	if err != nil {
		t.Fatal(err)
	}
	if git("", "rev-parse", "HEAD") != base || git("", "status", "--porcelain") != before {
		t.Fatal("original checkout changed")
	}
	if git("", "show", result.Commit+":a.txt") != "alpha" || git("", "show", result.Commit+":b.txt") != "beta" {
		t.Fatal("integration content")
	}
	if _, err = buildPeerCandidate(ctx, repo, base, "fixture", []string{a, b}); err == nil {
		t.Fatal("existing ref overwritten")
	}
	if _, err = buildPeerCandidate(ctx, repo, base, "conflict", []string{a, conflict}); err == nil {
		t.Fatal("conflict accepted")
	}
	if cmd := exec.Command("git", "-C", repo, "show-ref", "--verify", "refs/heads/codex/integration-conflict"); cmd.Run() == nil {
		t.Fatal("conflicting candidate published")
	}
	if _, err = buildPeerCandidate(ctx, repo, base, "../../main", []string{a}); err == nil {
		t.Fatal("unsafe ref accepted")
	}
}

package codexappserver

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"syscall"

	"github.com/aoagents/agent-orchestrator/backend/internal/attachmentstore"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"github.com/aoagents/agent-orchestrator/backend/internal/preview"
	"github.com/aoagents/agent-orchestrator/backend/internal/sessionartifacts"
)

type imageArtifacts struct {
	source, destination string
	session             domain.SessionID
}

func imageArtifactPaths(dataDir string, id domain.SessionID, env map[string]string) imageArtifacts {
	home := env["CODEX_HOME"]
	if home == "" {
		home = os.Getenv("CODEX_HOME")
	}
	if home == "" {
		userHome, _ := os.UserHomeDir()
		if userHome != "" {
			home = filepath.Join(userHome, ".codex")
		}
	}
	dest := sessionartifacts.Dir(dataDir, id)
	if !filepath.IsAbs(home) || !filepath.IsAbs(dest) || id == "" || !filepath.IsLocal(string(id)) || filepath.Base(string(id)) != string(id) {
		return imageArtifacts{}
	}
	return imageArtifacts{source: filepath.Join(home, "generated_images"), destination: dest, session: id}
}

func (a imageArtifacts) copy(thread, saved string) (string, error) {
	if a.source == "" || filepath.Base(thread) != thread || thread == "." || thread == "" || !filepath.IsLocal(thread) || !filepath.IsAbs(saved) {
		return "", fmt.Errorf("unknown image storage")
	}
	root := filepath.Join(a.source, thread)
	rel, err := filepath.Rel(root, saved)
	if err != nil || !filepath.IsLocal(rel) || filepath.Base(rel) != rel {
		return "", fmt.Errorf("image does not belong to this Codex thread")
	}
	source, err := os.OpenRoot(a.source)
	if err != nil {
		return "", err
	}
	defer source.Close()
	dir, err := source.Lstat(thread)
	if err != nil || !dir.IsDir() {
		return "", fmt.Errorf("image thread directory is unavailable")
	}
	threadRoot, err := source.OpenRoot(thread)
	if err != nil {
		return "", err
	}
	defer threadRoot.Close()
	actual, err := threadRoot.Stat(".")
	if err != nil || !os.SameFile(dir, actual) {
		return "", fmt.Errorf("image thread directory changed")
	}
	file, err := threadRoot.OpenFile(rel, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return "", err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return "", fmt.Errorf("generated image is not a regular file")
	}
	if info.Size() > attachmentstore.MaxFileBytes {
		return "", fmt.Errorf("image exceeds the artifact size limit")
	}
	data, err := io.ReadAll(io.LimitReader(file, attachmentstore.MaxFileBytes+1))
	if err != nil || len(data) > attachmentstore.MaxFileBytes {
		return "", fmt.Errorf("cannot read generated image within the artifact size limit")
	}
	var ext string
	switch http.DetectContentType(data) {
	case "image/png":
		ext = ".png"
	case "image/jpeg":
		ext = ".jpg"
	case "image/webp":
		ext = ".webp"
	case "image/gif":
		ext = ".gif"
	default:
		return "", fmt.Errorf("unrecognized image format")
	}
	digest := sha256.Sum256(data)
	name := fmt.Sprintf("generated-image-%x%s", digest, ext)
	if err := os.MkdirAll(a.destination, 0o700); err != nil {
		return "", err
	}
	dest, err := os.OpenRoot(a.destination)
	if err != nil {
		return "", err
	}
	defer dest.Close()
	var nonce [12]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return "", err
	}
	part := fmt.Sprintf(".generated-image-%x", nonce)
	out, err := dest.OpenFile(part, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return "", err
	}
	defer dest.Remove(part)
	_, writeErr := out.Write(data)
	closeErr := out.Close()
	if writeErr != nil || closeErr != nil {
		return "", fmt.Errorf("could not save the generated image")
	}
	if err := dest.Link(part, name); os.IsExist(err) {
		existing, _, _, readErr := preview.OpenWorkspaceFile(a.destination, name)
		if readErr != nil {
			return "", readErr
		}
		defer existing.Close()
		hash := sha256.New()
		n, readErr := io.Copy(hash, io.LimitReader(existing, int64(len(data))+1))
		if readErr != nil || n != int64(len(data)) || !bytes.Equal(hash.Sum(nil), digest[:]) {
			return "", fmt.Errorf("image artifact conflicts with an existing file")
		}
	} else if err != nil {
		return "", err
	}
	return name, nil
}

func (c *conversation) publishGeneratedImage(ev ports.ChatEvent, rootConversation bool) ports.ChatEvent {
	var detail struct {
		Event     string `json:"event"`
		SavedPath string `json:"savedPath"`
	}
	if ev.Kind != ports.ChatEventActivityCompleted || ev.ActivityKind != domain.ActivityKindSystem || json.Unmarshal(ev.Detail, &detail) != nil || detail.Event != "image.generated" {
		return ev
	}
	// Never persist the raw path. A child or a replaced provider cannot expose its files in this session.
	ev.Detail = encodeDetail(map[string]any{"event": "image.generated"})
	if !rootConversation || ev.ActivityStatus == domain.ActivityStatusFailed {
		return ev
	}
	name, err := c.imageArtifacts.copy(c.threadID, detail.SavedPath)
	if err != nil {
		c.log.Debug("could not import Codex-generated image", "error", err)
		ev.Summary = "Image generated, but preview could not be saved"
		return ev
	}
	ev.Detail = encodeDetail(map[string]any{
		"event": "image.generated",
		"image": map[string]string{
			"name": "Codex 生成的图片",
			"url":  "/api/v1/sessions/" + url.PathEscape(string(c.imageArtifacts.session)) + "/artifact-files/" + name,
		},
	})
	return ev
}

package codexappserver

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestGeneratedImageArtifactStaysWithinItsThread(t *testing.T) {
	home, data := t.TempDir(), t.TempDir()
	artifacts := imageArtifactPaths(data, "session", map[string]string{"CODEX_HOME": home})
	root := filepath.Join(artifacts.source, "thread")
	other := filepath.Join(artifacts.source, "other")
	for _, dir := range []string{root, other} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	png := []byte("\x89PNG\r\n\x1a\nfixture")
	original, foreign := filepath.Join(root, "result.png"), filepath.Join(other, "result.png")
	for _, file := range []string{original, foreign} {
		if err := os.WriteFile(file, png, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	name, err := artifacts.copy("thread", original)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := artifacts.copy("thread", original); err != nil || got != name {
		t.Fatalf("second import = %q, %v; want %q", got, err, name)
	}
	files, err := os.ReadDir(artifacts.destination)
	if err != nil || len(files) != 1 {
		t.Fatalf("published files = %v, %v; want one image", files, err)
	}
	content, err := os.ReadFile(filepath.Join(artifacts.destination, name))
	if err != nil || !bytes.Equal(content, png) {
		t.Fatalf("published image did not match the native output: %v", err)
	}
	link := filepath.Join(root, "link.png")
	if err := os.Symlink("../other/result.png", link); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(artifacts.source, "alias")
	if err := os.Symlink("other", alias); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ thread, file string }{
		{"thread", foreign},
		{"thread", link},
		{"alias", filepath.Join(alias, "result.png")},
	} {
		if _, err := artifacts.copy(tc.thread, tc.file); err == nil {
			t.Errorf("accepted image outside the native thread")
		}
	}
}

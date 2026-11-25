//go:build darwin || linux

package conpty

import (
	"context"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestDefaultSpawnHostEndToEnd(t *testing.T) {
	// Consume the DA1 reply in noncanonical mode, as a terminal application
	// does. Otherwise the shell's first line read also gets the reply bytes.
	const script = `
set -e
settings=$(stty -g)
stty -icanon -echo min 1 time 0
printf '\033[c'
reply=$(dd bs=1 count="$1" 2>/dev/null)
stty "$settings"
if [ "$reply" != "$2" ]; then
	printf 'unexpected DA1 reply: %s\n' "$reply"
	exit 1
fi
printf 'ready:%s:%s\n' "$AO_DIRECT_PTY_TEST" "$AO_PREFIX_VALUE"
IFS= read -r line
printf 'received:%s\n' "$line"
sleep 30
`
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	addr, hostPID, err := defaultSpawnHost(ctx, "spawn-e2e", t.TempDir(), []string{
		"env", "AO_PREFIX_VALUE=prefix", "/bin/sh", "-c",
		script, "pty-host-fixture", strconv.Itoa(len(da1Reply)), string(da1Reply),
	}, map[string]string{"AO_DIRECT_PTY_TEST": "works"}, HostOptions{})
	// The request context owns startup only. A host that reported READY must
	// stay alive after that request ends so daemon restarts cannot kill agents.
	cancel()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = clientKill(addr)
		if pidAlive(hostPID) {
			if process, findErr := os.FindProcess(hostPID); findErr == nil {
				_ = process.Kill()
			}
		}
	})

	waitForOutput := func(want string) {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		for {
			output, err := clientGetOutput(ctx, addr, 20)
			if err != nil {
				t.Fatalf("get output while waiting for %q: %v", want, err)
			}
			if strings.Contains(strings.ReplaceAll(output, "\r", ""), want) {
				return
			}
			select {
			case <-ctx.Done():
				t.Fatalf("timed out waiting for %q, output: %q", want, output)
			case <-time.After(10 * time.Millisecond):
			}
		}
	}

	waitForOutput("ready:works:prefix\n")
	if err := clientSendInput(addr, "hello\n"); err != nil {
		t.Fatalf("send input: %v", err)
	}
	waitForOutput("received:hello\n")

	if err := clientKill(addr); err != nil {
		t.Fatalf("kill host: %v", err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for pidAlive(hostPID) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if pidAlive(hostPID) {
		t.Fatalf("detached pty-host pid %d survived kill", hostPID)
	}
}

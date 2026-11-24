package agyacp

import (
	"context"
	"net"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// The bundled Python transport lacks python-socks. Prefer the HTTP endpoints
// already configured in macOS, never a direct connection or a guessed proxy.
// Explicit environment configuration and PAC remain authoritative.
func applySystemHTTPProxy(ctx context.Context, env map[string]string) {
	if runtime.GOOS != "darwin" {
		return
	}
	for _, key := range []string{"http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy", "no_proxy"} {
		for _, name := range []string{key, strings.ToUpper(key)} {
			if _, ok := env[name]; ok {
				return
			}
			if _, ok := os.LookupEnv(name); ok {
				return
			}
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	raw, err := exec.CommandContext(ctx, "/usr/sbin/scutil", "--proxy").Output()
	if err != nil {
		return
	}
	for key, value := range systemHTTPProxy(string(raw)) {
		env[key] = value
	}
}

func systemHTTPProxy(raw string) map[string]string {
	fields := map[string]string{}
	var exceptions []string
	inExceptions := false
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "ExceptionsList :") {
			inExceptions = true
			continue
		}
		if line == "}" {
			inExceptions = false
			continue
		}
		key, value, ok := strings.Cut(line, " : ")
		if !ok {
			continue
		}
		if inExceptions {

			exceptions = append(exceptions, strings.TrimPrefix(value, "*"))
		} else {
			fields[key] = value
		}
	}
	if fields["SOCKSEnable"] != "1" || fields["ProxyAutoConfigEnable"] == "1" || fields["ProxyAutoDiscoveryEnable"] == "1" {
		return nil
	}
	result := map[string]string{}
	for _, protocol := range []string{"HTTP", "HTTPS"} {
		port, err := strconv.Atoi(fields[protocol+"Port"])
		host := fields[protocol+"Proxy"]
		if fields[protocol+"Enable"] != "1" || host == "" || err != nil || port < 1 || port > 65535 {
			return nil
		}
		result[strings.ToLower(protocol)+"_proxy"] = (&url.URL{Scheme: "http", Host: net.JoinHostPort(host, strconv.Itoa(port))}).String()
	}
	result["no_proxy"] = strings.Join(exceptions, ",")
	return result
}

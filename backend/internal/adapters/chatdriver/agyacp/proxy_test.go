package agyacp

import (
	"strings"
	"testing"
)

func TestSystemHTTPProxyRequiresConfiguredEndpoints(t *testing.T) {
	raw := `SOCKSEnable : 1
HTTPEnable : 1
HTTPProxy : 127.0.0.1
HTTPPort : 7897
HTTPSEnable : 1
HTTPSProxy : 127.0.0.1
HTTPSPort : 7897
ExceptionsList : <array> {
0 : localhost
1 : *.local
}`
	got := systemHTTPProxy(raw)
	if got["https_proxy"] != "http://127.0.0.1:7897" || got["no_proxy"] != "localhost,.local" {
		t.Fatalf("unexpected proxy: %#v", got)
	}
	for _, invalid := range []string{raw + "\nProxyAutoConfigEnable : 1", strings.ReplaceAll(raw, "HTTPSPort : 7897", "HTTPSPort : invalid"), strings.ReplaceAll(raw, "HTTPSEnable : 1", "HTTPSEnable : 0"), strings.ReplaceAll(raw, "SOCKSEnable : 1", "SOCKSEnable : 0")} {
		if result := systemHTTPProxy(invalid); result != nil {
			t.Fatalf("must preserve original handling: %#v", result)
		}
	}
}

package peerpolicy

import (
	"context"
	"errors"
	"testing"
)

type fake struct{ err error }

func (f fake) PeerSessionBound(_ context.Context, id string) (bool, error) {
	return id == "bound", f.err
}
func TestAdmissionScope(t *testing.T) {
	ctx := context.Background()
	s := fake{}
	for _, pair := range [][2]string{{"bound", "outside"}, {"outside", "bound"}, {"", "bound"}} {
		if Check(ctx, s, pair[0], pair[1]) == nil {
			t.Fatal("legacy bypass", pair)
		}
	}
	if Check(ctx, s, "a", "b") != nil {
		t.Fatal("unrelated session blocked")
	}
	admitted := Admitted(ctx, "bound", "outside")
	if Check(admitted, s, "bound", "outside") != nil {
		t.Fatal("reserved delivery blocked")
	}
	if Check(admitted, s, "bound", "another") == nil {
		t.Fatal("admission escaped target")
	}
	if Check(ctx, fake{errors.New("db unavailable")}, "a", "b") == nil {
		t.Fatal("lookup failure opened access")
	}
}

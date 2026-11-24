package domain

import (
	"crypto/sha256"
	"fmt"
)

// PeerBroadcastMaxMessageBytes bounds user-authored group messages, not model context.
const PeerBroadcastMaxMessageBytes = 256 * 1024

// Zero-value settings preserve enabled, unlimited participation.
type PeerMemberSettings struct {
	Disabled bool `json:"disabled"`
	// nil: unlimited; zero: cannot initiate peer messages; positive: per-round cap.
	PeerLimit *int `json:"peerLimit,omitempty"`
}
type PeerRoom struct {
	MemberSettings map[string]PeerMemberSettings `json:"memberSettings,omitempty"`
	ID             string                        `json:"id"`
	ProjectID      string                        `json:"projectId"`
	Members        []string                      `json:"members"`
	Epoch          int64                         `json:"epoch"`
	Budget         int                           `json:"budget"`
	Used           int                           `json:"used"`
	Paused         bool                          `json:"paused"`
}
type PeerAttachment struct {
	Name     string `json:"name"`
	MimeType string `json:"mimeType"`
	Size     int    `json:"size"`
	SHA256   string `json:"sha256"`
}
type PeerMessage struct {
	RoundID     string           `json:"roundId,omitempty"`
	Attachments []PeerAttachment `json:"attachments,omitempty"`
	BroadcastID string           `json:"broadcastId,omitempty"`
	Sender      string           `json:"sender"`
	Target      string           `json:"target"`
	ID          string           `json:"id"`
	Epoch       int64            `json:"epoch"`
	Message     string           `json:"message"`
}

type PeerTask struct {
	ID           string `json:"id"`
	Goal         string `json:"goal"`
	State        string `json:"state"`
	Owner        string `json:"owner"`
	Revision     int64  `json:"revision"`
	Evidence     string `json:"evidence"`
	Commit       string `json:"commit"`
	Reviewer     string `json:"reviewer"`
	DeliveryID   string `json:"deliveryId,omitempty"`
	ReviewCommit string `json:"reviewCommit,omitempty"`
	ReviewIssue  string `json:"reviewIssue,omitempty"`
}
type PeerTestEvidence struct {
	Command  string `json:"command"`
	Cwd      string `json:"cwd"`
	ExitCode int    `json:"exitCode"`
	Scope    string `json:"scope"`
}
type PeerTaskAction struct {
	// Recorded by the store from the active native turn, never from the caller.
	DeliveryID string             `json:"deliveryId,omitempty"`
	Files      []string           `json:"files,omitempty"`
	Tests      []PeerTestEvidence `json:"tests,omitempty"`
	Executions []string           `json:"executions,omitempty"`

	Goal     string `json:"goal,omitempty"`
	ID       string `json:"id"`
	Actor    string `json:"actor"`
	Epoch    int64  `json:"epoch"`
	Revision int64  `json:"revision"`
	Action   string `json:"action"`
	Evidence string `json:"evidence"`
	Commit   string `json:"commit"`
}

// PeerDelivery describes durable admission, not a completed agent turn.
// Empty metadata belongs to a delivery recorded before history support.
type PeerDelivery struct {
	GeneratedImages  []PeerGeneratedImage `json:"generatedImages,omitempty"`
	Attachments      []PeerAttachment     `json:"attachments,omitempty"`
	BroadcastID      string               `json:"broadcastId,omitempty"`
	Detail           string               `json:"detail,omitempty"`
	TurnState        string               `json:"turnState,omitempty"`
	Replies          []PeerReply          `json:"replies"`
	RepliesTruncated bool                 `json:"repliesTruncated"`
	ID               string               `json:"id"`
	Sender           string               `json:"sender"`
	Target           string               `json:"target"`
	Message          string               `json:"message"`
	Epoch            int64                `json:"epoch"`
	State            string               `json:"state"`
	CreatedAt        string               `json:"createdAt"`
}

type PeerGeneratedImage struct {
	ID     string         `json:"id"`
	Status ActivityStatus `json:"status"`
	Name   string         `json:"name,omitempty"`
	URL    string         `json:"url,omitempty"`
}

func PeerDeliveryKey(room, id string) string {
	return fmt.Sprintf("peer:%x", sha256.Sum256([]byte(room+"\x00"+id)))
}

type PeerReply struct {
	CreatedAt string `json:"createdAt"`
	ID        string `json:"id"`
	Text      string `json:"text"`
	Streaming bool   `json:"streaming"`
	TurnState string `json:"turnState"`
}

type PeerToolBinding struct {
	RoomID    string `json:"roomId"`
	ProjectID string `json:"projectId"`
	Epoch     int64  `json:"epoch"`
}
type PeerBindRequest struct {
	Epoch int64 `json:"epoch"`
}

// PeerExecution is a projection of an existing native turn, not a second executor.
type PeerExecution struct {
	SessionID      string                 `json:"sessionId"`
	Epoch          int64                  `json:"epoch"`
	ConversationID string                 `json:"conversationId"`
	TurnID         string                 `json:"turnId"`
	ProviderTurnID string                 `json:"providerTurnId"`
	State          string                 `json:"state"`
	Activities     []ConversationActivity `json:"-"`
	Truncated      bool                   `json:"truncated"`
}

// Manual records are user-reported evidence, never proof that this platform ran a test.
type PeerIntegrationCheck struct {
	ID        string `json:"id"`
	Commit    string `json:"commit"`
	Executor  string `json:"executor"`
	Command   string `json:"command"`
	Cwd       string `json:"cwd"`
	ExitCode  int    `json:"exitCode"`
	Scope     string `json:"scope"`
	CreatedAt string `json:"createdAt"`
	Epoch     int64  `json:"epoch"`
}
type PeerCandidate struct {
	Commit    string                 `json:"commit"`
	Branch    string                 `json:"branch"`
	Tasks     []string               `json:"tasks"`
	CreatedAt string                 `json:"createdAt"`
	Checks    []PeerIntegrationCheck `json:"checks"`
}

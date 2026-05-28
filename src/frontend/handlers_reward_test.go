package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func requestWithSession(method, path, body, sessionID string) *http.Request {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req = req.WithContext(context.WithValue(req.Context(), ctxKeySessionID{}, sessionID))
	req.Header.Set("Content-Type", "application/json")
	return req
}

func resetFrontendCountersForTest() {
	frontendCountersMu.Lock()
	defer frontendCountersMu.Unlock()
	frontendCounters = map[string]int64{}
}

func TestWatchAdForwardsWatchID(t *testing.T) {
	var got map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/earn" {
			t.Fatalf("path = %q, want /earn", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Fatalf("decode reward payload: %v", err)
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"coins_added": 5,
			"balance":     5,
			"stage":       1,
		})
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(
		http.MethodPost,
		"/ads/watch",
		`{"ad_id":"ad-watch-001","stage":1,"watch_id":"watch-123","style":"modal","show_in":"home"}`,
		"session-1",
	)

	fe.watchAdHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got["session_id"] != "session-1" {
		t.Fatalf("session_id = %#v", got["session_id"])
	}
	if got["watch_id"] != "watch-123" {
		t.Fatalf("watch_id = %#v", got["watch_id"])
	}
}

func TestWatchStartProxyAddsSessionID(t *testing.T) {
	var got map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ads/watch/start" {
			t.Fatalf("path = %q, want /ads/watch/start", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Fatalf("decode start payload: %v", err)
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"watch_id":       "watch-123",
			"expires_in_sec": 1800,
			"stages":         []map[string]int{{"stage": 1, "trigger_sec": 10, "coins": 5}},
		})
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(
		http.MethodPost,
		"/ads/watch/start",
		`{"ad_id":"ad-watch-001","creative_id":"creative-watch-video-001","campaign_id":"campaign-reward-video-demo","duration_ms":30000}`,
		"session-1",
	)

	fe.watchAdStartHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got["session_id"] != "session-1" {
		t.Fatalf("session_id = %#v", got["session_id"])
	}
}

func TestWatchEventProxyAddsSessionID(t *testing.T) {
	var got map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ads/watch/event" {
			t.Fatalf("path = %q, want /ads/watch/event", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Fatalf("decode event payload: %v", err)
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"watch_id":        "watch-123",
			"max_position_ms": 12000,
		})
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(
		http.MethodPost,
		"/ads/watch/event",
		`{"watch_id":"watch-123","ad_id":"ad-watch-001","creative_id":"creative-watch-video-001","campaign_id":"campaign-reward-video-demo","event":"timeupdate","position_ms":12000,"duration_ms":30000}`,
		"session-1",
	)

	fe.watchAdEventHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got["session_id"] != "session-1" {
		t.Fatalf("session_id = %#v", got["session_id"])
	}
}

func TestWatchEventProxyBucketsUnknownEventMetric(t *testing.T) {
	resetFrontendCountersForTest()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"watch_id":        "watch-123",
			"max_position_ms": 12000,
		})
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(
		http.MethodPost,
		"/ads/watch/event",
		`{"watch_id":"watch-123","ad_id":"ad-watch-001","event":"custom-client-event-with-user-data","position_ms":12000,"duration_ms":30000}`,
		"session-1",
	)

	fe.watchAdEventHandler(rec, req)

	metrics := frontendCounters
	if metrics[`ad_watch_event_proxy_total{event="other",result="success"}`] != 1 {
		t.Fatalf("unknown event metric = %#v", metrics)
	}
	if metrics[`ad_watch_event_proxy_total{event="custom-client-event-with-user-data",result="success"}`] != 0 {
		t.Fatalf("raw client event should not be used as a metric label: %#v", metrics)
	}
}

func TestMetricsHandlerIncludesWatchProxyCounters(t *testing.T) {
	resetFrontendCountersForTest()

	frontendCounterInc(`ad_watch_start_proxy_total{result="success"}`)
	frontendCounterInc(`ad_watch_event_proxy_total{event="timeupdate",result="success"}`)
	frontendCounterInc(`ad_watch_reward_proxy_total{result="success"}`)

	rec := httptest.NewRecorder()
	(&frontendServer{}).metricsHandler(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	body := rec.Body.String()

	for _, want := range []string{
		"# TYPE ad_watch_start_proxy_total counter",
		"# TYPE ad_watch_event_proxy_total counter",
		"# TYPE ad_watch_reward_proxy_total counter",
		`ad_watch_start_proxy_total{result="success"} 1`,
		`ad_watch_event_proxy_total{event="timeupdate",result="success"} 1`,
		`ad_watch_reward_proxy_total{result="success"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("metrics body missing %q in:\n%s", want, body)
		}
	}
}

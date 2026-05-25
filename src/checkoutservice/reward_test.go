package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestValidateCouponLocksCoupon(t *testing.T) {
	var gotPath string
	var gotPayload map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		json.NewEncoder(w).Encode(map[string]any{"valid": true, "discount_pct": 90})
	}))
	defer server.Close()

	cs := checkoutService{rewardServiceAddr: server.Listener.Addr().String()}

	discountPct, err := cs.validateCoupon(context.Background(), "session-1", "COIN-ABC123-0001")

	if err != nil {
		t.Fatalf("validateCoupon returned error: %v", err)
	}
	if discountPct != 90 {
		t.Fatalf("discountPct = %d, want 90", discountPct)
	}
	if gotPath != "/coupon/validate" {
		t.Fatalf("path = %q, want /coupon/validate", gotPath)
	}
	if gotPayload["session_id"] != "session-1" || gotPayload["coupon_code"] != "COIN-ABC123-0001" {
		t.Fatalf("payload = %#v", gotPayload)
	}
}

func TestCouponCommitAndCancelPostToRewardService(t *testing.T) {
	paths := []string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		json.NewEncoder(w).Encode(map[string]bool{"success": true})
	}))
	defer server.Close()

	cs := checkoutService{rewardServiceAddr: server.Listener.Addr().String()}

	if err := cs.commitCoupon(context.Background(), "COIN-ABC123-0001"); err != nil {
		t.Fatalf("commitCoupon returned error: %v", err)
	}
	if err := cs.cancelCoupon(context.Background(), "COIN-ABC123-0001"); err != nil {
		t.Fatalf("cancelCoupon returned error: %v", err)
	}
	if len(paths) != 2 || paths[0] != "/coupon/commit" || paths[1] != "/coupon/cancel" {
		t.Fatalf("paths = %#v", paths)
	}
}

func TestCheckoutMetricsHandlerWritesPrometheusCounters(t *testing.T) {
	atomic.StoreUint64(&checkoutSuccessTotal, 2)
	atomic.StoreUint64(&checkoutFailureTotal, 1)
	atomic.StoreUint64(&checkoutCouponValidateTotal, 3)
	atomic.StoreUint64(&checkoutCouponCommitTotal, 1)

	rec := httptest.NewRecorder()
	checkoutMetricsHandler(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	body := rec.Body.String()
	for _, want := range []string{
		"checkout_success_total 2",
		"checkout_failure_total 1",
		"checkout_coupon_validate_total 3",
		"checkout_coupon_commit_total 1",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("metrics body missing %q in:\n%s", want, body)
		}
	}
}

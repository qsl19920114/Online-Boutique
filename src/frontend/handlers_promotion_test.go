package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPromotionSummaryIncludesProductActivitiesAndCoupons(t *testing.T) {
	resetFrontendCountersForTest()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/coins/session-1":
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"session_id": "session-1",
				"balance":    75,
			})
		case "/subsidy/check":
			if got := r.URL.Query().Get("product_id"); got != "OLJCESPC7Z" {
				t.Fatalf("subsidy product_id = %q", got)
			}
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"product_id":   "OLJCESPC7Z",
				"has_subsidy":  true,
				"discount_pct": 20,
				"label":        "亿补价",
			})
		case "/flash/status":
			if got := r.URL.Query().Get("session_id"); got != "session-1" {
				t.Fatalf("flash session_id = %q", got)
			}
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"active":       true,
				"discount_pct": 70,
				"cost_coins":   0,
				"remaining":    12,
			})
		case "/rush/status":
			if got := r.URL.Query().Get("session_id"); got != "session-1" {
				t.Fatalf("rush session_id = %q", got)
			}
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"active":          true,
				"coins_per_claim": 10,
				"remaining":       60,
			})
		case "/coupons":
			if got := r.URL.Query().Get("session_id"); got != "session-1" {
				t.Fatalf("coupons session_id = %q", got)
			}
			status := r.URL.Query().Get("status")
			if status == "pending" {
				_ = json.NewEncoder(w).Encode(map[string]interface{}{
					"session_id": "session-1",
					"coupons": []map[string]interface{}{
						{
							"coupon_code":  "CPN-PENDING",
							"discount_pct": 90,
							"status":       "pending",
							"source":       "redeem",
							"cost_coins":   50,
						},
					},
				})
				return
			}
			if status == "locked" {
				_ = json.NewEncoder(w).Encode(map[string]interface{}{
					"session_id": "session-1",
					"coupons": []map[string]interface{}{
						{
							"coupon_code":  "CPN-LOCKED",
							"discount_pct": 80,
							"status":       "locked",
							"source":       "flash",
							"cost_coins":   0,
						},
					},
				})
				return
			}
			t.Fatalf("unexpected coupon status = %q", status)
		case "/ads/stage-config":
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"stages": []map[string]interface{}{
					{"stage": 1, "trigger_sec": 10, "coins": 5},
				},
			})
		default:
			t.Fatalf("unexpected rewardservice path %q", r.URL.String())
		}
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(http.MethodGet, "/promotion/summary?product_id=OLJCESPC7Z", "", "session-1")

	fe.promotionSummaryHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}

	var body struct {
		ProductID   string `json:"product_id"`
		CoinBalance int    `json:"coin_balance"`
		Subsidy     struct {
			Active      bool   `json:"active"`
			DiscountPct int    `json:"discount_pct"`
			Label       string `json:"label"`
		} `json:"subsidy"`
		Flash struct {
			Active      bool   `json:"active"`
			DiscountPct int    `json:"discount_pct"`
			CostCoins   int    `json:"cost_coins"`
			Remaining   int    `json:"remaining"`
			Source      string `json:"source"`
		} `json:"flash"`
		Rush struct {
			Active    bool `json:"active"`
			Coins     int  `json:"coins"`
			Remaining int  `json:"remaining"`
		} `json:"rush"`
		OwnedCoupons []struct {
			Code        string `json:"code"`
			DiscountPct int    `json:"discount_pct"`
			Status      string `json:"status"`
			Source      string `json:"source"`
			CostCoins   int    `json:"cost_coins"`
		} `json:"owned_coupons"`
		RedeemOptions []struct {
			CostCoins   int  `json:"cost_coins"`
			DiscountPct int  `json:"discount_pct"`
			Affordable  bool `json:"affordable"`
		} `json:"redeem_options"`
		AdRewards []struct {
			Stage      int `json:"stage"`
			TriggerSec int `json:"trigger_sec"`
			Coins      int `json:"coins"`
		} `json:"ad_rewards"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("decode summary: %v", err)
	}

	if body.ProductID != "OLJCESPC7Z" {
		t.Fatalf("product_id = %q", body.ProductID)
	}
	if body.CoinBalance != 75 {
		t.Fatalf("coin_balance = %d", body.CoinBalance)
	}
	if !body.Subsidy.Active || body.Subsidy.DiscountPct != 20 || body.Subsidy.Label != "亿补价" {
		t.Fatalf("subsidy = %#v", body.Subsidy)
	}
	if !body.Flash.Active || body.Flash.DiscountPct != 70 || body.Flash.Remaining != 12 || body.Flash.Source != "flash" {
		t.Fatalf("flash = %#v", body.Flash)
	}
	if !body.Rush.Active || body.Rush.Coins != 10 || body.Rush.Remaining != 60 {
		t.Fatalf("rush = %#v", body.Rush)
	}
	if len(body.OwnedCoupons) != 2 || body.OwnedCoupons[0].Code != "CPN-PENDING" || body.OwnedCoupons[1].Code != "CPN-LOCKED" {
		t.Fatalf("owned_coupons = %#v", body.OwnedCoupons)
	}
	if len(body.RedeemOptions) != 2 || !body.RedeemOptions[0].Affordable || body.RedeemOptions[1].Affordable {
		t.Fatalf("redeem_options = %#v", body.RedeemOptions)
	}
	if len(body.AdRewards) != 1 || body.AdRewards[0].Stage != 1 || body.AdRewards[0].Coins != 5 {
		t.Fatalf("ad_rewards = %#v", body.AdRewards)
	}
	if frontendCounters[`promotion_summary_view_total{page="product",has_product="true",result="success"}`] != 1 {
		t.Fatalf("promotion summary metric = %#v", frontendCounters)
	}
	for key := range frontendCounters {
		if strings.Contains(key, "OLJCESPC7Z") || strings.Contains(key, "session-1") || strings.Contains(key, "CPN-PENDING") {
			t.Fatalf("metric label should not contain raw identifiers: %s", key)
		}
	}
}

func TestCouponsProxyAddsSessionAndStatus(t *testing.T) {
	resetFrontendCountersForTest()

	var upstreamQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/coupons" {
			t.Fatalf("path = %q, want /coupons", r.URL.Path)
		}
		upstreamQuery = r.URL.RawQuery
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"session_id": "session-1",
			"coupons": []map[string]interface{}{
				{
					"coupon_code":  "CPN-PENDING",
					"discount_pct": 90,
					"status":       "pending",
				},
			},
			"count": 1,
		})
	}))
	defer server.Close()

	fe := frontendServer{rewardServiceAddr: server.Listener.Addr().String()}
	rec := httptest.NewRecorder()
	req := requestWithSession(http.MethodGet, "/coupons?status=pending", "", "session-1")

	fe.couponsProxyHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(upstreamQuery, "session_id=session-1") || !strings.Contains(upstreamQuery, "status=pending") {
		t.Fatalf("upstream query = %q", upstreamQuery)
	}
	if !strings.Contains(rec.Body.String(), "CPN-PENDING") {
		t.Fatalf("body should preserve upstream coupon payload, got %s", rec.Body.String())
	}
	if frontendCounters[`coupon_list_proxy_total{page="other",status="pending",result="success"}`] != 1 {
		t.Fatalf("coupon proxy metric = %#v", frontendCounters)
	}
	for key := range frontendCounters {
		if strings.Contains(key, "session-1") || strings.Contains(key, "CPN-PENDING") {
			t.Fatalf("metric label should not contain raw identifiers: %s", key)
		}
	}
}

func TestPromotionSummaryFallsBackWhenRewardServiceUnavailable(t *testing.T) {
	resetFrontendCountersForTest()

	fe := frontendServer{rewardServiceAddr: "127.0.0.1:1"}
	rec := httptest.NewRecorder()
	req := requestWithSession(http.MethodGet, "/promotion/summary?product_id=OLJCESPC7Z", "", "session-1")

	fe.promotionSummaryHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}

	var body struct {
		ProductID     string        `json:"product_id"`
		CoinBalance   int           `json:"coin_balance"`
		OwnedCoupons  []interface{} `json:"owned_coupons"`
		RedeemOptions []interface{} `json:"redeem_options"`
		AdRewards     []interface{} `json:"ad_rewards"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("decode summary: %v", err)
	}
	if body.ProductID != "OLJCESPC7Z" || body.CoinBalance != -1 {
		t.Fatalf("fallback body = %#v", body)
	}
	if len(body.OwnedCoupons) != 0 || len(body.RedeemOptions) != 2 || len(body.AdRewards) != 3 {
		t.Fatalf("fallback lists = coupons:%d redeem:%d ads:%d", len(body.OwnedCoupons), len(body.RedeemOptions), len(body.AdRewards))
	}
	if frontendCounters[`promotion_summary_view_total{page="product",has_product="true",result="failed"}`] != 1 {
		t.Fatalf("fallback metric = %#v", frontendCounters)
	}
}

// Copyright 2018 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"html/template"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/mux"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"

	pb "github.com/GoogleCloudPlatform/microservices-demo/src/frontend/genproto"
	"github.com/GoogleCloudPlatform/microservices-demo/src/frontend/money"
	"github.com/GoogleCloudPlatform/microservices-demo/src/frontend/validator"
)

type platformDetails struct {
	css      string
	provider string
}

var (
	frontendMessage    = strings.TrimSpace(os.Getenv("FRONTEND_MESSAGE"))
	isCymbalBrand      = "true" == strings.ToLower(os.Getenv("CYMBAL_BRANDING"))
	assistantEnabled   = "true" == strings.ToLower(os.Getenv("ENABLE_ASSISTANT"))
	frontendCounters   = map[string]int64{}
	frontendCountersMu sync.Mutex
	templates          = template.Must(template.New("").
				Funcs(template.FuncMap{
			"renderMoney":        renderMoney,
			"renderCurrencyLogo": renderCurrencyLogo,
		}).ParseGlob("templates/*.html"))
	plat platformDetails
)

var validEnvs = []string{"local", "gcp", "azure", "aws", "onprem", "alibaba"}

func (fe *frontendServer) homeHandler(w http.ResponseWriter, r *http.Request) {
	frontendCounterInc(`page_view_total{page="home"}`)
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.WithField("currency", currentCurrency(r)).Info("home")
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	products, err := fe.getProducts(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve products"), http.StatusInternalServerError)
		return
	}
	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	type productView struct {
		Item  *pb.Product
		Price *pb.Money
	}
	ps := make([]productView, len(products))
	for i, p := range products {
		price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "failed to do currency conversion for product %s", p.GetId()), http.StatusInternalServerError)
			return
		}
		ps[i] = productView{p, price}
	}

	// Set ENV_PLATFORM (default to local if not set; use env var if set; otherwise detect GCP, which overrides env)_
	var env = os.Getenv("ENV_PLATFORM")
	// Only override from env variable if set + valid env
	if env == "" || stringinSlice(validEnvs, env) == false {
		fmt.Println("env platform is either empty or invalid")
		env = "local"
	}
	// Autodetect GCP
	addrs, err := net.LookupHost("metadata.google.internal.")
	if err == nil && len(addrs) >= 0 {
		log.Debugf("Detected Google metadata server: %v, setting ENV_PLATFORM to GCP.", addrs)
		env = "gcp"
	}

	log.Debugf("ENV_PLATFORM is: %s", env)
	plat = platformDetails{}
	plat.setPlatformDetails(strings.ToLower(env))

	if err := templates.ExecuteTemplate(w, "home", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency": true,
		"currencies":    currencies,
		"products":      ps,
		"cart_size":     cartSize(cart),
		"banner_color":  os.Getenv("BANNER_COLOR"), // illustrates canary deployments
		"ad":            fe.chooseAd(r.Context(), []string{}, log),
		"coin_balance":  fe.rewardBalanceOrDefault(r.Context(), sessionID(r)),
	})); err != nil {
		log.Error(err)
	}
}

func (plat *platformDetails) setPlatformDetails(env string) {
	if env == "aws" {
		plat.provider = "AWS"
		plat.css = "aws-platform"
	} else if env == "onprem" {
		plat.provider = "On-Premises"
		plat.css = "onprem-platform"
	} else if env == "azure" {
		plat.provider = "Azure"
		plat.css = "azure-platform"
	} else if env == "gcp" {
		plat.provider = "Google Cloud"
		plat.css = "gcp-platform"
	} else if env == "alibaba" {
		plat.provider = "Alibaba Cloud"
		plat.css = "alibaba-platform"
	} else {
		plat.provider = "local"
		plat.css = "local"
	}
}

func (fe *frontendServer) productHandler(w http.ResponseWriter, r *http.Request) {
	frontendCounterInc(`page_view_total{page="product"}`)
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	if id == "" {
		renderHTTPError(log, r, w, errors.New("product id not specified"), http.StatusBadRequest)
		return
	}
	log.WithField("id", id).WithField("currency", currentCurrency(r)).
		Debug("serving product page")

	p, err := fe.getProduct(r.Context(), id)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve product"), http.StatusInternalServerError)
		return
	}
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to convert currency"), http.StatusInternalServerError)
		return
	}

	// ignores the error retrieving recommendations since it is not critical
	recommendations, err := fe.getRecommendations(r.Context(), sessionID(r), []string{id})
	if err != nil {
		log.WithField("error", err).Warn("failed to get product recommendations")
	}

	product := struct {
		Item  *pb.Product
		Price *pb.Money
	}{p, price}

	// Fetch packaging info (weight/dimensions) of the product
	// The packaging service is an optional microservice you can run as part of a Google Cloud demo.
	var packagingInfo *PackagingInfo = nil
	if isPackagingServiceConfigured() {
		packagingInfo, err = httpGetPackagingInfo(id)
		if err != nil {
			fmt.Println("Failed to obtain product's packaging info:", err)
		}
	}

	if err := templates.ExecuteTemplate(w, "product", injectCommonTemplateData(r, map[string]interface{}{
		"ad":              fe.chooseAd(r.Context(), p.Categories, log),
		"show_currency":   true,
		"currencies":      currencies,
		"product":         product,
		"recommendations": recommendations,
		"cart_size":       cartSize(cart),
		"packagingInfo":   packagingInfo,
		"coin_balance":    fe.rewardBalanceOrDefault(r.Context(), sessionID(r)),
		"subsidy":         fe.getSubsidyInfo(r.Context(), id),
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) addToCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	quantity, _ := strconv.ParseUint(r.FormValue("quantity"), 10, 32)
	productID := r.FormValue("product_id")
	payload := validator.AddToCartPayload{
		Quantity:  quantity,
		ProductID: productID,
	}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}
	log.WithField("product", payload.ProductID).WithField("quantity", payload.Quantity).Debug("adding to cart")

	p, err := fe.getProduct(r.Context(), payload.ProductID)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve product"), http.StatusInternalServerError)
		return
	}

	if err := fe.insertCart(r.Context(), sessionID(r), p.GetId(), int32(payload.Quantity)); err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to add to cart"), http.StatusInternalServerError)
		return
	}
	w.Header().Set("location", baseUrl+"/cart")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) emptyCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("emptying cart")

	if err := fe.emptyCart(r.Context(), sessionID(r)); err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to empty cart"), http.StatusInternalServerError)
		return
	}
	w.Header().Set("location", baseUrl+"/")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) viewCartHandler(w http.ResponseWriter, r *http.Request) {
	frontendCounterInc(`page_view_total{page="cart"}`)
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("view user cart")
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	// ignores the error retrieving recommendations since it is not critical
	recommendations, err := fe.getRecommendations(r.Context(), sessionID(r), cartIDs(cart))
	if err != nil {
		log.WithField("error", err).Warn("failed to get product recommendations")
	}

	shippingCost, err := fe.getShippingQuote(r.Context(), cart, currentCurrency(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to get shipping quote"), http.StatusInternalServerError)
		return
	}

	type cartItemView struct {
		Item     *pb.Product
		Quantity int32
		Price    *pb.Money
	}
	items := make([]cartItemView, len(cart))
	totalPrice := pb.Money{CurrencyCode: currentCurrency(r)}
	for i, item := range cart {
		p, err := fe.getProduct(r.Context(), item.GetProductId())
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "could not retrieve product #%s", item.GetProductId()), http.StatusInternalServerError)
			return
		}
		price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "could not convert currency for product #%s", item.GetProductId()), http.StatusInternalServerError)
			return
		}

		multPrice := money.MultiplySlow(*price, uint32(item.GetQuantity()))
		items[i] = cartItemView{
			Item:     p,
			Quantity: item.GetQuantity(),
			Price:    &multPrice}
		totalPrice = money.Must(money.Sum(totalPrice, multPrice))
	}
	totalPrice = money.Must(money.Sum(totalPrice, *shippingCost))
	year := time.Now().Year()

	if err := templates.ExecuteTemplate(w, "cart", injectCommonTemplateData(r, map[string]interface{}{
		"currencies":       currencies,
		"recommendations":  recommendations,
		"cart_size":        cartSize(cart),
		"shipping_cost":    shippingCost,
		"show_currency":    true,
		"total_cost":       totalPrice,
		"items":            items,
		"expiration_years": []int{year, year + 1, year + 2, year + 3, year + 4},
		"coin_balance":     fe.rewardBalanceOrDefault(r.Context(), sessionID(r)),
		"ad":               fe.chooseAd(r.Context(), cartIDs(cart), log),
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) placeOrderHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("placing order")

	var (
		email         = r.FormValue("email")
		streetAddress = r.FormValue("street_address")
		zipCode, _    = strconv.ParseInt(r.FormValue("zip_code"), 10, 32)
		city          = r.FormValue("city")
		state         = r.FormValue("state")
		country       = r.FormValue("country")
		ccNumber      = r.FormValue("credit_card_number")
		ccMonth, _    = strconv.ParseInt(r.FormValue("credit_card_expiration_month"), 10, 32)
		ccYear, _     = strconv.ParseInt(r.FormValue("credit_card_expiration_year"), 10, 32)
		ccCVV, _      = strconv.ParseInt(r.FormValue("credit_card_cvv"), 10, 32)
		couponCode    = strings.ToUpper(strings.TrimSpace(r.FormValue("coupon_code")))
	)

	payload := validator.PlaceOrderPayload{
		Email:         email,
		StreetAddress: streetAddress,
		ZipCode:       zipCode,
		City:          city,
		State:         state,
		Country:       country,
		CcNumber:      ccNumber,
		CcMonth:       ccMonth,
		CcYear:        ccYear,
		CcCVV:         ccCVV,
	}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}

	order, err := pb.NewCheckoutServiceClient(fe.checkoutSvcConn).
		PlaceOrder(r.Context(), &pb.PlaceOrderRequest{
			Email: payload.Email,
			CreditCard: &pb.CreditCardInfo{
				CreditCardNumber:          payload.CcNumber,
				CreditCardExpirationMonth: int32(payload.CcMonth),
				CreditCardExpirationYear:  int32(payload.CcYear),
				CreditCardCvv:             int32(payload.CcCVV)},
			UserId:       sessionID(r),
			UserCurrency: currentCurrency(r),
			CouponCode:   couponCode,
			Address: &pb.Address{
				StreetAddress: payload.StreetAddress,
				City:          payload.City,
				State:         payload.State,
				ZipCode:       int32(payload.ZipCode),
				Country:       payload.Country},
		})
	if err != nil {
		frontendCounterInc("checkout_failure_total")
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to complete the order"), http.StatusInternalServerError)
		return
	}
	frontendCounterInc("checkout_success_total")
	log.WithField("order", order.GetOrder().GetOrderId()).Info("order placed")

	order.GetOrder().GetItems()
	recommendations, _ := fe.getRecommendations(r.Context(), sessionID(r), nil)

	totalPaid := order.GetOrder().GetTotalPaid()
	if totalPaid == nil {
		computed := *order.GetOrder().GetShippingCost()
		for _, v := range order.GetOrder().GetItems() {
			multPrice := money.MultiplySlow(*v.GetCost(), uint32(v.GetItem().GetQuantity()))
			computed = money.Must(money.Sum(computed, multPrice))
		}
		totalPaid = &computed
	}

	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	if err := templates.ExecuteTemplate(w, "order", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency":   false,
		"currencies":      currencies,
		"order":           order.GetOrder(),
		"total_paid":      totalPaid,
		"discount_amount": order.GetOrder().GetDiscountAmount(),
		"coupon_code":     order.GetOrder().GetCouponCode(),
		"recommendations": recommendations,
		"coin_balance":    fe.rewardBalanceOrDefault(r.Context(), sessionID(r)),
		"ad":              fe.chooseAd(r.Context(), nil, log),
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) assistantHandler(w http.ResponseWriter, r *http.Request) {
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	if err := templates.ExecuteTemplate(w, "assistant", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency": false,
		"currencies":    currencies,
	})); err != nil {
		log.Println(err)
	}
}

type rewardBalanceResponse struct {
	SessionID string `json:"session_id"`
	Balance   int    `json:"balance"`
}

type rewardEarnRequest struct {
	SessionID string `json:"session_id"`
	AdID      string `json:"ad_id"`
	Stage     int    `json:"stage"`
}

type rewardRedeemRequest struct {
	SessionID string `json:"session_id"`
	Cost      int    `json:"cost"`
}

type rewardRedeemResponse struct {
	CouponCode       string `json:"coupon_code"`
	DiscountPct      int    `json:"discount_pct"`
	RemainingBalance int    `json:"remaining_balance"`
}

type rewardCheckinStatusResponse struct {
	SessionID   string                 `json:"session_id"`
	Today       string                 `json:"today"`
	CheckedIn   bool                   `json:"checked_in"`
	Streak      int                    `json:"streak"`
	NextReward  int                    `json:"next_reward"`
	WeeklyBonus bool                   `json:"weekly_bonus"`
	Calendar    []rewardCheckinDayView `json:"calendar"`
}

type rewardCheckinDayView struct {
	Date    string `json:"date"`
	Checked bool   `json:"checked"`
	Coins   int    `json:"coins"`
}

type rewardCheckinResponse struct {
	CoinsAdded  int    `json:"coins_added"`
	Balance     int    `json:"balance"`
	Streak      int    `json:"streak"`
	WeeklyBonus bool   `json:"weekly_bonus"`
	Today       string `json:"today"`
}

func (fe *frontendServer) watchAdHandler(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		AdID   string `json:"ad_id"`
		Stage  int    `json:"stage"`
		Style  string `json:"style"`
		ShowIn string `json:"show_in"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	frontendCounterInc(fmt.Sprintf(
		`ad_click_total{style="%s",show_in="%s"}`,
		sanitizeMetricLabel(payload.Style),
		sanitizeMetricLabel(payload.ShowIn),
	))
	var out map[string]interface{}
	err := fe.rewardPost(r.Context(), "/earn", rewardEarnRequest{
		SessionID: sessionID(r),
		AdID:      payload.AdID,
		Stage:     payload.Stage,
	}, &out)
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	if payload.Stage == 3 {
		frontendCounterInc("ad_watch_complete_total")
	}
	out["ok"] = true
	writeJSON(w, out)
}

func (fe *frontendServer) adStageConfigHandler(w http.ResponseWriter, r *http.Request) {
	var out map[string]interface{}
	if err := fe.rewardGet(r.Context(), "/ads/stage-config", &out); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	writeJSON(w, out)
}

func (fe *frontendServer) rewardsPageHandler(w http.ResponseWriter, r *http.Request) {
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	balance := fe.rewardBalanceOrDefault(r.Context(), sessionID(r))
	checkinStatus, _ := fe.getCheckinStatus(r.Context(), sessionID(r))
	if err := templates.ExecuteTemplate(w, "rewards", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency":  false,
		"currencies":     currencies,
		"coin_balance":   balance,
		"checkin_status": checkinStatus,
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) redeemHandler(w http.ResponseWriter, r *http.Request) {
	frontendCounterInc("coupon_apply_total")
	cost, _ := strconv.Atoi(r.FormValue("cost"))
	var redeemed rewardRedeemResponse
	err := fe.rewardPost(r.Context(), "/redeem", rewardRedeemRequest{
		SessionID: sessionID(r),
		Cost:      cost,
	}, &redeemed)
	currencies, currencyErr := fe.getCurrencies(r.Context())
	if currencyErr != nil {
		renderHTTPError(log, r, w, errors.Wrap(currencyErr, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	payload := map[string]interface{}{
		"show_currency":  false,
		"currencies":     currencies,
		"coin_balance":   fe.rewardBalanceOrDefault(r.Context(), sessionID(r)),
		"checkin_status": nil,
	}
	payload["checkin_status"], _ = fe.getCheckinStatus(r.Context(), sessionID(r))
	if err != nil {
		payload["redeem_error"] = err.Error()
	} else {
		payload["redeemed_coupon"] = redeemed
		payload["coin_balance"] = redeemed.RemainingBalance
	}
	if err := templates.ExecuteTemplate(w, "rewards", injectCommonTemplateData(r, payload)); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) checkInStatusHandler(w http.ResponseWriter, r *http.Request) {
	status, err := fe.getCheckinStatus(r.Context(), sessionID(r))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	writeJSON(w, status)
}

func (fe *frontendServer) checkInHandler(w http.ResponseWriter, r *http.Request) {
	frontendCounterInc("checkin_button_click_total")
	var out rewardCheckinResponse
	err := fe.rewardPost(r.Context(), "/checkin", map[string]string{
		"session_id": sessionID(r),
	}, &out)
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, map[string]interface{}{
		"ok":           true,
		"coins_added":  out.CoinsAdded,
		"balance":      out.Balance,
		"streak":       out.Streak,
		"weekly_bonus": out.WeeklyBonus,
		"today":        out.Today,
	})
}

// ────────────────────────────── 秒杀 / 整点抢金币 / 百亿补贴 ──────────────────── //

func (fe *frontendServer) flashStatusHandler(w http.ResponseWriter, r *http.Request) {
	sid := sessionID(r)
	result, err := fe.rewardGetRaw(r.Context(), "/flash/status?session_id="+sid)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(result)
}

func (fe *frontendServer) flashClaimHandler(w http.ResponseWriter, r *http.Request) {
	sid := sessionID(r)
	body, _ := json.Marshal(map[string]string{"session_id": sid})
	result, statusCode, err := fe.rewardPostRaw(r.Context(), "/flash/claim", body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	w.Write(result)
}

func (fe *frontendServer) rushStatusHandler(w http.ResponseWriter, r *http.Request) {
	sid := sessionID(r)
	result, err := fe.rewardGetRaw(r.Context(), "/rush/status?session_id="+sid)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(result)
}

func (fe *frontendServer) rushClaimHandler(w http.ResponseWriter, r *http.Request) {
	sid := sessionID(r)
	body, _ := json.Marshal(map[string]string{"session_id": sid})
	result, statusCode, err := fe.rewardPostRaw(r.Context(), "/rush/claim", body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	w.Write(result)
}

func (fe *frontendServer) subsidyCheckHandler(w http.ResponseWriter, r *http.Request) {
	pid := r.URL.Query().Get("product_id")
	result, err := fe.rewardGetRaw(r.Context(), "/subsidy/check?product_id="+pid)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(result)
}

func (fe *frontendServer) trackHandler(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		Event string `json:"event"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)
	if payload.Event != "" {
		frontendCounterInc("track_total")
	}
	writeJSON(w, map[string]bool{"ok": true})
}

func (fe *frontendServer) metricsHandler(w http.ResponseWriter, r *http.Request) {
	frontendCountersMu.Lock()
	defer frontendCountersMu.Unlock()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintln(w, "# TYPE page_view_total counter")
	fmt.Fprintln(w, "# TYPE ad_click_total counter")
	fmt.Fprintln(w, "# TYPE ad_watch_complete_total counter")
	fmt.Fprintln(w, "# TYPE coupon_apply_total counter")
	fmt.Fprintln(w, "# TYPE checkout_success_total counter")
	fmt.Fprintln(w, "# TYPE checkout_failure_total counter")
	fmt.Fprintln(w, "# TYPE track_total counter")
	fmt.Fprintln(w, "# TYPE checkin_button_click_total counter")
	for name, value := range frontendCounters {
		fmt.Fprintf(w, "%s %d\n", name, value)
	}
}

func (fe *frontendServer) rewardBalanceOrDefault(ctx context.Context, sessionID string) int {
	balance, err := fe.getRewardBalance(ctx, sessionID)
	if err != nil {
		return -1
	}
	return balance
}

func (fe *frontendServer) getRewardBalance(ctx context.Context, sessionID string) (int, error) {
	var out rewardBalanceResponse
	if err := fe.rewardGet(ctx, "/coins/"+sessionID, &out); err != nil {
		return 0, err
	}
	return out.Balance, nil
}

func (fe *frontendServer) getCheckinStatus(ctx context.Context, sessionID string) (*rewardCheckinStatusResponse, error) {
	var out rewardCheckinStatusResponse
	path := "/checkin/status?session_id=" + url.QueryEscape(sessionID)
	if err := fe.rewardGet(ctx, path, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (fe *frontendServer) getSubsidyInfo(ctx context.Context, productID string) map[string]interface{} {
	b, err := fe.rewardGetRaw(ctx, "/subsidy/check?product_id="+productID)
	if err != nil {
		return nil
	}
	var result struct {
		HasSubsidy  bool   `json:"has_subsidy"`
		DiscountPct int    `json:"discount_pct"`
		Label       string `json:"label"`
	}
	if err := json.Unmarshal(b, &result); err != nil || !result.HasSubsidy {
		return nil
	}
	return map[string]interface{}{
		"has_subsidy":  result.HasSubsidy,
		"discount_pct": result.DiscountPct,
		"label":        result.Label,
	}
}

func (fe *frontendServer) rewardGetRaw(ctx context.Context, path string) ([]byte, error) {
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, "http://"+fe.rewardServiceAddr+path, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	return b, nil
}

func (fe *frontendServer) rewardPostRaw(ctx context.Context, path string, body []byte) ([]byte, int, error) {
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, "http://"+fe.rewardServiceAddr+path, bytes.NewReader(body))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, resp.StatusCode, err
	}
	return b, resp.StatusCode, nil
}

func (fe *frontendServer) rewardGet(ctx context.Context, path string, out interface{}) error {
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, "http://"+fe.rewardServiceAddr+path, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return decodeRewardResponse(resp, path, out)
}

func (fe *frontendServer) rewardPost(ctx context.Context, path string, payload interface{}, out interface{}) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, "http://"+fe.rewardServiceAddr+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return decodeRewardResponse(resp, path, out)
}

func decodeRewardResponse(resp *http.Response, path string, out interface{}) error {
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("rewardservice %s returned %d: %s", path, resp.StatusCode, string(body))
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(body, out)
}

func writeJSON(w http.ResponseWriter, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(payload)
}

func frontendCounterInc(name string) {
	frontendCountersMu.Lock()
	defer frontendCountersMu.Unlock()
	frontendCounters[name]++
}

func sanitizeMetricLabel(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "unknown"
	}
	var out strings.Builder
	for _, item := range value {
		if item >= 'a' && item <= 'z' || item >= 'A' && item <= 'Z' || item >= '0' && item <= '9' || item == '-' || item == '_' {
			out.WriteRune(item)
		}
	}
	if out.Len() == 0 {
		return "unknown"
	}
	return out.String()
}

func (fe *frontendServer) logoutHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("logging out")
	for _, c := range r.Cookies() {
		c.Expires = time.Now().Add(-time.Hour * 24 * 365)
		c.MaxAge = -1
		http.SetCookie(w, c)
	}
	w.Header().Set("Location", baseUrl+"/")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) getProductByID(w http.ResponseWriter, r *http.Request) {
	id := mux.Vars(r)["ids"]
	if id == "" {
		return
	}

	p, err := fe.getProduct(r.Context(), id)
	if err != nil {
		return
	}

	jsonData, err := json.Marshal(p)
	if err != nil {
		fmt.Println(err)
		return
	}

	w.Write(jsonData)
	w.WriteHeader(http.StatusOK)
}

func (fe *frontendServer) chatBotHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	type Response struct {
		Message string `json:"message"`
	}

	type LLMResponse struct {
		Content string         `json:"content"`
		Details map[string]any `json:"details"`
	}

	var response LLMResponse

	url := "http://" + fe.shoppingAssistantSvcAddr
	req, err := http.NewRequest(http.MethodPost, url, r.Body)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to create request"), http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to send request"), http.StatusInternalServerError)
		return
	}

	body, err := io.ReadAll(res.Body)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to read response"), http.StatusInternalServerError)
		return
	}

	fmt.Printf("%+v\n", body)
	fmt.Printf("%+v\n", res)

	err = json.Unmarshal(body, &response)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to unmarshal body"), http.StatusInternalServerError)
		return
	}

	// respond with the same message
	json.NewEncoder(w).Encode(Response{Message: response.Content})

	w.WriteHeader(http.StatusOK)
}

func (fe *frontendServer) setCurrencyHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	cur := r.FormValue("currency_code")
	payload := validator.SetCurrencyPayload{Currency: cur}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}
	log.WithField("curr.new", payload.Currency).WithField("curr.old", currentCurrency(r)).
		Debug("setting currency")

	if payload.Currency != "" {
		http.SetCookie(w, &http.Cookie{
			Name:   cookieCurrency,
			Value:  payload.Currency,
			MaxAge: cookieMaxAge,
		})
	}
	referer := r.Header.Get("referer")
	if referer == "" {
		referer = baseUrl + "/"
	}
	w.Header().Set("Location", referer)
	w.WriteHeader(http.StatusFound)
}

// chooseAd queries for advertisements available and randomly chooses one, if
// available. It ignores the error retrieving the ad since it is not critical.
func (fe *frontendServer) chooseAd(ctx context.Context, ctxKeys []string, log logrus.FieldLogger) *pb.Ad {
	ads, err := fe.getAd(ctx, ctxKeys)
	if err != nil {
		log.WithField("error", err).Warn("failed to retrieve ads")
		return nil
	}
	if len(ads) == 0 {
		return nil
	}
	return ads[rand.Intn(len(ads))]
}

func renderHTTPError(log logrus.FieldLogger, r *http.Request, w http.ResponseWriter, err error, code int) {
	log.WithField("error", err).Error("request error")
	errMsg := fmt.Sprintf("%+v", err)

	w.WriteHeader(code)

	if templateErr := templates.ExecuteTemplate(w, "error", injectCommonTemplateData(r, map[string]interface{}{
		"error":       errMsg,
		"status_code": code,
		"status":      http.StatusText(code),
	})); templateErr != nil {
		log.Println(templateErr)
	}
}

func injectCommonTemplateData(r *http.Request, payload map[string]interface{}) map[string]interface{} {
	data := map[string]interface{}{
		"session_id":        sessionID(r),
		"request_id":        r.Context().Value(ctxKeyRequestID{}),
		"user_currency":     currentCurrency(r),
		"platform_css":      plat.css,
		"platform_name":     plat.provider,
		"is_cymbal_brand":   isCymbalBrand,
		"assistant_enabled": assistantEnabled,
		"deploymentDetails": deploymentDetailsMap,
		"frontendMessage":   frontendMessage,
		"currentYear":       time.Now().Year(),
		"baseUrl":           baseUrl,
		"coin_balance":      -1,
	}

	for k, v := range payload {
		data[k] = v
	}

	return data
}

func currentCurrency(r *http.Request) string {
	c, _ := r.Cookie(cookieCurrency)
	if c != nil {
		return c.Value
	}
	return defaultCurrency
}

func sessionID(r *http.Request) string {
	v := r.Context().Value(ctxKeySessionID{})
	if v != nil {
		return v.(string)
	}
	return ""
}

func cartIDs(c []*pb.CartItem) []string {
	out := make([]string, len(c))
	for i, v := range c {
		out[i] = v.GetProductId()
	}
	return out
}

// get total # of items in cart
func cartSize(c []*pb.CartItem) int {
	cartSize := 0
	for _, item := range c {
		cartSize += int(item.GetQuantity())
	}
	return cartSize
}

func renderMoney(money pb.Money) string {
	currencyLogo := renderCurrencyLogo(money.GetCurrencyCode())
	return fmt.Sprintf("%s%d.%02d", currencyLogo, money.GetUnits(), money.GetNanos()/10000000)
}

func renderCurrencyLogo(currencyCode string) string {
	logos := map[string]string{
		"USD": "$",
		"CAD": "$",
		"JPY": "¥",
		"EUR": "€",
		"TRY": "₺",
		"GBP": "£",
	}

	logo := "$" //default
	if val, ok := logos[currencyCode]; ok {
		logo = val
	}
	return logo
}

func stringinSlice(slice []string, val string) bool {
	for _, item := range slice {
		if item == val {
			return true
		}
	}
	return false
}

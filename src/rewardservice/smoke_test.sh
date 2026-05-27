#!/usr/bin/env bash
set -e
BASE=http://127.0.0.1:8090
SID=demo-session-$(date +%s)

ok()  { echo -e "\033[32m✓ $1\033[0m"; }
fail(){ echo -e "\033[31m✗ $1\033[0m"; exit 1; }
sep() { echo -e "\n\033[1m── $1 ──\033[0m"; }

sep "1. 健康检查"
R=$(curl -sf $BASE/_healthz); echo "$R"
[ "$R" = "ok" ] && ok "healthz" || fail "healthz"

sep "2. 金币余额（初始为 0）"
curl -sf "$BASE/coins/$SID" | python3 -m json.tool

sep "3. 看广告赚金币 stage1=5coins"
curl -sf -X POST $BASE/earn \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"ad_id\":\"ad-001\",\"stage\":1}" | python3 -m json.tool

sep "4. 看广告赚金币 stage3=20coins"
curl -sf -X POST $BASE/earn \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"ad_id\":\"ad-002\",\"stage\":3}" | python3 -m json.tool

sep "5. 金币余额应为 25"
curl -sf "$BASE/coins/$SID" | python3 -m json.tool

sep "6. 冷却期重复广告 → 429"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST $BASE/earn \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"ad_id\":\"ad-001\",\"stage\":2}")
[ "$STATUS" = "429" ] && ok "cooldown 429" || fail "expected 429 got $STATUS"

sep "7. 每日签到"
curl -sf -X POST $BASE/checkin \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\"}" | python3 -m json.tool

sep "8. 签到日历状态"
curl -sf "$BASE/checkin/status?session_id=$SID" | python3 -m json.tool

sep "9. 重复签到 → 409"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST $BASE/checkin \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\"}")
[ "$STATUS" = "409" ] && ok "duplicate checkin 409" || fail "expected 409 got $STATUS"

# 再多看两个广告，确保余额 ≥ 50
sep "补充金币：再看两个广告（各 20 coins）"
curl -sf -X POST $BASE/earn -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"ad_id\":\"ad-003\",\"stage\":3}" | python3 -m json.tool
curl -sf -X POST $BASE/earn -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"ad_id\":\"ad-004\",\"stage\":3}" | python3 -m json.tool

sep "10. 用 50 金币兑换 9折 优惠券"
REDEEM=$(curl -sf -X POST $BASE/redeem \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"cost\":50}")
echo "$REDEEM" | python3 -m json.tool
COUPON=$(echo "$REDEEM" | python3 -c "import sys,json; print(json.load(sys.stdin)['coupon_code'])")
echo "  优惠券码: $COUPON"

sep "11. 验证优惠券（→ locked）"
curl -sf -X POST $BASE/coupon/validate \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"coupon_code\":\"$COUPON\"}" | python3 -m json.tool

sep "12. 核销优惠券（结算）"
curl -sf -X POST $BASE/coupon/commit \
  -H 'Content-Type: application/json' \
  -d "{\"coupon_code\":\"$COUPON\"}" | python3 -m json.tool

sep "13. 交易流水（earn×2 + checkin + redeem）"
curl -sf "$BASE/coins/$SID/transactions" | python3 -m json.tool

sep "14. 百亿补贴 - 已配置商品"
curl -sf "$BASE/subsidy/check?product_id=OLJCESPC7Z" | python3 -m json.tool

sep "15. 百亿补贴 - 未知商品"
curl -sf "$BASE/subsidy/check?product_id=NOTEXIST" | python3 -m json.tool

sep "16. 百亿补贴商品列表"
curl -sf "$BASE/subsidy/products" | python3 -m json.tool

sep "17. 广告阶段配置"
curl -sf "$BASE/ads/stage-config" | python3 -m json.tool

sep "18. 整点抢金币状态"
curl -sf "$BASE/rush/status?session_id=$SID" | python3 -m json.tool

sep "19. 秒杀状态"
curl -sf "$BASE/flash/status?session_id=$SID" | python3 -m json.tool

sep "20. 优惠券列表"
curl -sf "$BASE/coupons?session_id=$SID" | python3 -m json.tool

sep "21. Prometheus 指标（前 15 行）"
curl -sf "$BASE/metrics" | grep "^coins_earned\|^checkin_total\|^coupon_redeemed\|^coupon_used" | head -15

echo -e "\n\033[32m=== 全部冒烟测试通过 ===\033[0m"

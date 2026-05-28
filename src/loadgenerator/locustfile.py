#!/usr/bin/python
#
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
import time
from locust import FastHttpUser, TaskSet, between
from faker import Faker
import datetime
fake = Faker()

products = [
    '0PUK6V6EV0',
    '1YMWWN1N4O',
    '2ZYFJ3GM2N',
    '66VCHSJNUP',
    '6E92ZMYYFZ',
    '9SIQT8TOJO',
    'L9ECAV7KIM',
    'LS4PSXUNUM',
    'OLJCESPC7Z']

reward_ads = [
    {
        'ad_id': 'ad-hairdryer-001',
        'creative_id': 'creative-hairdryer-video-001',
        'campaign_id': 'campaign-reward-video-demo',
        'duration_ms': 30000,
        'style': 'card',
        'show_in': 'home',
    },
    {
        'ad_id': 'ad-tank-top-001',
        'creative_id': 'creative-tank-top-video-001',
        'campaign_id': 'campaign-reward-video-demo',
        'duration_ms': 30000,
        'style': 'wide-card',
        'show_in': 'recommendations',
    },
    {
        'ad_id': 'ad-candle-holder-001',
        'creative_id': 'creative-candle-holder-video-001',
        'campaign_id': 'campaign-reward-video-demo',
        'duration_ms': 30000,
        'style': 'banner',
        'show_in': 'product',
    },
    {
        'ad_id': 'ad-watch-001',
        'creative_id': 'creative-watch-video-001',
        'campaign_id': 'campaign-reward-video-demo',
        'duration_ms': 30000,
        'style': 'float',
        'show_in': 'global',
    },
]

fallback_stages = [
    {'stage': 1, 'trigger_sec': 10, 'coins': 5},
    {'stage': 2, 'trigger_sec': 20, 'coins': 12},
    {'stage': 3, 'trigger_sec': 30, 'coins': 20},
]

def index(l):
    l.client.get("/")

def setCurrency(l):
    currencies = ['EUR', 'USD', 'JPY', 'CAD', 'GBP', 'TRY']
    l.client.post("/setCurrency",
        {'currency_code': random.choice(currencies)})

def browseProduct(l):
    l.client.get("/product/" + random.choice(products))

def viewCart(l):
    l.client.get("/cart")

def addToCart(l):
    product = random.choice(products)
    l.client.get("/product/" + product)
    l.client.post("/cart", {
        'product_id': product,
        'quantity': random.randint(1,10)})
    
def empty_cart(l):
    l.client.post('/cart/empty')

def checkout(l):
    addToCart(l)
    current_year = datetime.datetime.now().year+1
    l.client.post("/cart/checkout", {
        'email': fake.email(),
        'street_address': fake.street_address(),
        'zip_code': fake.zipcode(),
        'city': fake.city(),
        'state': fake.state_abbr(),
        'country': fake.country(),
        'credit_card_number': fake.credit_card_number(card_type="visa"),
        'credit_card_expiration_month': random.randint(1, 12),
        'credit_card_expiration_year': random.randint(current_year, current_year + 70),
        'credit_card_cvv': f"{random.randint(100, 999)}",
    })
    
def logout(l):
    l.client.get('/logout')  

def watchVideoAd(l):
    ad = random.choice(reward_ads)
    start_response = l.client.post("/ads/watch/start", json={
        'ad_id': ad['ad_id'],
        'creative_id': ad['creative_id'],
        'campaign_id': ad['campaign_id'],
        'duration_ms': ad['duration_ms'],
    }, name="/ads/watch/start")
    if start_response.status_code != 200:
        return
    try:
        start_body = start_response.json()
    except ValueError:
        return

    watch_id = start_body.get('watch_id')
    if not watch_id:
        return

    stages = start_body.get('stages') or fallback_stages
    target_stage = random.choices([1, 2, 3], weights=[6, 3, 1])[0]
    target = next((item for item in stages if item.get('stage') == target_stage), stages[0])
    trigger_ms = int(target.get('trigger_sec', 10)) * 1000

    _post_watch_event(l, ad, watch_id, 'loadedmetadata', 0)
    _post_watch_event(l, ad, watch_id, 'playing', 0)

    started = time.monotonic()
    position_ms = 0
    while position_ms < trigger_ms:
        time.sleep(min(2.5, max(0.1, (trigger_ms - position_ms) / 1000.0)))
        position_ms = min(trigger_ms, int((time.monotonic() - started) * 1000))
        event = 'waiting' if random.random() < 0.04 else 'timeupdate'
        event_response = _post_watch_event(l, ad, watch_id, event, position_ms)
        if event_response is not None and event_response.status_code >= 400:
            return

    if target_stage == 3:
        _post_watch_event(l, ad, watch_id, 'ended', trigger_ms)
    if random.random() < 0.01:
        _post_watch_event(l, ad, watch_id, 'error', position_ms, 'network')

    with l.client.post("/ads/watch", json={
        'ad_id': ad['ad_id'],
        'stage': target_stage,
        'watch_id': watch_id,
        'style': ad['style'],
        'show_in': ad['show_in'],
    }, name="/ads/watch", catch_response=True) as reward_response:
        if reward_response.status_code == 429:
            try:
                body = reward_response.json()
            except ValueError:
                body = {}
            if body.get('error') == 'ad reward is cooling down':
                reward_response.success()

def _post_watch_event(l, ad, watch_id, event, position_ms, error_type=''):
    return l.client.post("/ads/watch/event", json={
        'watch_id': watch_id,
        'ad_id': ad['ad_id'],
        'creative_id': ad['creative_id'],
        'campaign_id': ad['campaign_id'],
        'event': event,
        'position_ms': int(position_ms),
        'duration_ms': ad['duration_ms'],
        'error_type': error_type,
    }, name="/ads/watch/event")


class UserBehavior(TaskSet):

    def on_start(self):
        index(self)

    tasks = {index: 1,
        setCurrency: 2,
        browseProduct: 10,
        addToCart: 2,
        viewCart: 3,
        checkout: 1,
        watchVideoAd: 2}

class WebsiteUser(FastHttpUser):
    tasks = [UserBehavior]
    wait_time = between(1, 10)

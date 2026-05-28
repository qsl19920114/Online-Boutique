#!/usr/bin/env python3
import argparse
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

INSTALL_PLAYWRIGHT = (
    "python3 -m pip install playwright && "
    "python3 -m playwright install chromium"
)

DASHBOARDS = [
    {
        "uid": "rewardservice-overview",
        "slug": "rewardservice-overview",
        "filename": "rewardservice-overview.png",
    },
    {
        "uid": "ad-video-stability",
        "slug": "ad-video-stability",
        "filename": "ad-video-stability.png",
    },
    {
        "uid": "product-promotion-closed-loop",
        "slug": "product-promotion-closed-loop",
        "filename": "product-promotion-closed-loop.png",
    },
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture Grafana dashboard screenshots for monitoring docs."
    )
    parser.add_argument(
        "--grafana-url",
        default="http://127.0.0.1:3000",
        help="Grafana base URL. Default: http://127.0.0.1:3000",
    )
    parser.add_argument(
        "--out-dir",
        default="docs/screenshots/grafana",
        help="Base output directory. A timestamped child directory is created.",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        default="now-6h",
        help="Grafana time range start. Default: now-6h",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        default="now",
        help="Grafana time range end. Default: now",
    )
    return parser.parse_args()


def normalize_base_url(raw_url):
    return raw_url.rstrip("/")


def ensure_grafana_reachable(grafana_url):
    health_url = urljoin(f"{grafana_url}/", "api/health")
    try:
        with urlopen(health_url, timeout=5):
            return
    except HTTPError:
        return
    except (OSError, socket.timeout, URLError) as exc:
        print(
            f"Grafana is not reachable at {grafana_url}. "
            "Start Grafana or run: kubectl -n monitoring port-forward "
            "svc/grafana 3000:3000",
            file=sys.stderr,
        )
        print(f"Connection error: {exc}", file=sys.stderr)
        sys.exit(3)


def import_playwright():
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ModuleNotFoundError as exc:
        if exc.name == "playwright":
            print(
                "Playwright is required. Install it with: "
                f"{INSTALL_PLAYWRIGHT}",
                file=sys.stderr,
            )
            sys.exit(2)
        raise
    return Error, TimeoutError, sync_playwright


def dashboard_url(grafana_url, dashboard, from_time, to_time):
    query = urlencode({"orgId": "1", "from": from_time, "to": to_time})
    return (
        f"{grafana_url}/d/{dashboard['uid']}/{dashboard['slug']}"
        f"?{query}&kiosk"
    )


def timestamped_output_dir(base_dir):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(base_dir) / timestamp
    suffix = 1
    while output_dir.exists():
        output_dir = Path(base_dir) / f"{timestamp}-{suffix}"
        suffix += 1
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def maybe_login(page, grafana_url):
    if page.locator("input[name='user']").count() == 0:
        return

    username = os.environ.get("GRAFANA_USER", "admin")
    password = os.environ.get("GRAFANA_PASSWORD", "admin")
    page.fill("input[name='user']", username)
    page.fill("input[name='password']", password)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle", timeout=15000)

    skip_button = page.get_by_role("button", name="Skip")
    if skip_button.count() > 0:
        skip_button.click()
        page.wait_for_load_state("networkidle", timeout=15000)

    page.goto(grafana_url, wait_until="networkidle", timeout=30000)


def wait_for_dashboard(page, timeout_error):
    try:
        page.wait_for_selector(
            ".react-grid-layout, [data-testid='dashboard-container']",
            timeout=15000,
        )
    except timeout_error:
        pass
    page.wait_for_timeout(3000)


def capture(args):
    grafana_url = normalize_base_url(args.grafana_url)
    playwright_error, timeout_error, sync_playwright = import_playwright()
    ensure_grafana_reachable(grafana_url)
    output_dir = timestamped_output_dir(args.out_dir)

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except playwright_error as exc:
                print(
                    "Chromium for Playwright is required. Install it with: "
                    f"{INSTALL_PLAYWRIGHT}",
                    file=sys.stderr,
                )
                print(f"Launch error: {exc}", file=sys.stderr)
                sys.exit(2)

            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            page.goto(grafana_url, wait_until="networkidle", timeout=30000)
            maybe_login(page, grafana_url)

            for dashboard in DASHBOARDS:
                url = dashboard_url(
                    grafana_url,
                    dashboard,
                    args.from_time,
                    args.to_time,
                )
                destination = output_dir / dashboard["filename"]
                page.goto(url, wait_until="networkidle", timeout=30000)
                wait_for_dashboard(page, timeout_error)
                page.screenshot(path=str(destination), full_page=True)
                print(f"captured {destination}")

            browser.close()
    except playwright_error as exc:
        print(f"Failed to capture Grafana screenshots: {exc}", file=sys.stderr)
        sys.exit(4)

    print(f"Grafana screenshots written to {output_dir}")


def main():
    args = parse_args()
    capture(args)


if __name__ == "__main__":
    main()

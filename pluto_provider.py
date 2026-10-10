import requests
import json
import uuid
import os
import glob
import sys
import re
from datetime import datetime
from typing import List, Dict, Any


class BaseProvider:
    def __init__(self, name):
        self.name = name

    def get_user_agent(self):
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"

    def get_timeout(self):
        return 30


class PlutoProvider(BaseProvider):
    """Provider for Pluto TV with HD Resolution, Categories, and Regional EPGs"""

    def __init__(self):
        super().__init__("pluto")

        self.device_id = str(uuid.uuid1())
        self.session_token = None
        self.stitcher_params = ""
        self.session_expires_at = 0

        # Configuration from environment (e.g., 'us', 'gb', 'ca')
        self.region = os.getenv("PLUTO_REGION", "us").lower()

        # Regional IP mappings retained from the original provider.
        # Only regions used by the GitHub Actions workflow are included.
        self.x_forward = {
            "us": "185.236.200.172",
            "ca": "192.206.151.131",
            "gb": "84.17.50.173",
            "de": "217.94.184.66",
            "mx": "200.68.128.83",
            "no": "78.26.38.103",
            "se": "185.6.8.2",
            "dk": "192.36.27.7",
        }

        self.headers = {
            "authority": "boot.pluto.tv",
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://pluto.tv",
            "referer": "https://pluto.tv/",
            "user-agent": self.get_user_agent(),
        }

        # Restore the original regional forwarding header.
        if self.region in self.x_forward:
            self.headers["X-Forwarded-For"] = self.x_forward[self.region]

    def _get_session_token(self) -> str:
        if (
            self.session_token
            and datetime.now().timestamp() < self.session_expires_at
        ):
            return self.session_token

        try:
            url = "https://boot.pluto.tv/v4/start"

            params = {
                "appName": "web",
                "appVersion": "8.1.0",
                "deviceVersion": "133.0.0",
                "deviceModel": "web",
                "deviceMake": "chrome",
                "deviceType": "web",
                "clientID": self.device_id,
                "clientModelNumber": "1.0.0",
                "serverSideAds": "false",
                "architecture": "x86_64",
                "buildVersion": "1.0.0",
                "drmCapabilities": "widevine:L3",
            }

            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.get_timeout(),
            )

            response.raise_for_status()
            data = response.json()

            self.session_token = data.get("sessionToken", "")
            self.stitcher_params = data.get("stitcherParams", "")
            self.session_expires_at = (
                datetime.now().timestamp() + (4 * 3600)
            )

            return self.session_token

        except Exception as exc:
            print(
                f"[{self.region.upper()}] Session token error: {exc}",
                file=sys.stderr,
            )
            return ""

    def _get_categories(self, headers: dict) -> dict:
        """Fetch category names for the current regional request."""

        try:
            url = (
                "https://service-channels.clusters.pluto.tv/"
                "v2/guide/categories"
            )

            response = requests.get(
                url,
                headers=headers,
                timeout=self.get_timeout(),
            )

            response.raise_for_status()
            data = response.json().get("data", [])

            cat_map = {}

            for category in data:
                cat_name = category.get("name", "General")

                for channel_id in category.get("channelIDs", []):
                    cat_map[channel_id] = cat_name

            return cat_map

        except Exception as exc:
            print(
                f"[{self.region.upper()}] Category request error: {exc}",
                file=sys.stderr,
            )
            return {}

    def get_channels(self) -> List[Dict[str, Any]]:
        try:
            token = self._get_session_token()

            if not token:
                print(
                    f"[{self.region.upper()}] No session token received.",
                    file=sys.stderr,
                )
                return []

            url = (
                "https://service-channels.clusters.pluto.tv/"
                "v2/guide/channels"
            )

            # Copy the regional headers, including X-Forwarded-For.
            headers = self.headers.copy()
            headers["authorization"] = f"Bearer {token}"

            response = requests.get(
                url,
                params={"limit": "1000"},
                headers=headers,
                timeout=self.get_timeout(),
            )

            response.raise_for_status()
            channel_data = response.json().get("data", [])

            print(
                f"[{self.region.upper()}] "
                f"Received {len(channel_data)} channels from the API."
            )

            categories_list = self._get_categories(headers)

            processed_channels = []

            for channel in channel_data:
                channel_id = channel.get("id")
                name = channel.get("name")

                if not channel_id or not name:
                    continue

                logo = next(
                    (
                        img.get("url")
                        for img in channel.get("images", [])
                        if img.get("type") == "colorLogoPNG"
                    ),
                    "",
                )

                group = categories_list.get(channel_id, "Pluto TV")

                sid = str(uuid.uuid4())

                quality_suffix = (
                    "&quality=720p&deviceMake=chrome&deviceType=web"
                    "&deviceModel=web&deviceVersion=133.0.0"
                    "&architecture=x86_64&buildVersion=1.0.0"
                    "&includeExtendedEvents=true"
                    "&masterJWTPassthrough=true"
                )

                stream_url = (
                    "https://cfd-v4-service-channel-stitcher-use1-1."
                    "prd.pluto.tv/v2/stitch/hls/channel/"
                    f"{channel_id}/master.m3u8"
                    f"?{self.stitcher_params}&jwt={token}"
                    f"{quality_suffix}"
                )

                processed_channels.append(
                    {
                        "id": str(channel_id),
                        "name": name,
                        "stream_url": stream_url,
                        "logo": logo,
                        "group": group,
                    }
                )

            print(
                f"[{self.region.upper()}] "
                f"Processed {len(processed_channels)} channels."
            )

            return processed_channels

        except Exception as exc:
            print(
                f"[{self.region.upper()}] Channel request error: {exc}",
                file=sys.stderr,
            )
            return []

    def generate_m3u(self, channels):
        # Use the EPG URL corresponding to the selected region.
        m3u = (
            '#EXTM3U url-tvg="https://github.com/'
            'matthuisman/i.mjh.nz/raw/master/PlutoTV/'
            f'{self.region}.xml.gz"\n'
        )

        for ch in channels:
            m3u += (
                f'#EXTINF:-1 tvg-id="{ch["id"]}" '
                f'tvg-logo="{ch["logo"]}" '
                f'group-title="{ch["group"]}",{ch["name"]}\n'
            )

            m3u += f'{ch["stream_url"]}\n'

        return m3u


def merge_master_playlist():
    """Combine regional playlists and label each channel by country."""

    sort_config = {
        "us": {"priority": 1, "label": "United States"},
        "ca": {"priority": 2, "label": "Canada"},
        "gb": {"priority": 3, "label": "United Kingdom"},
        "fr": {"priority": 4, "label": "France"},
        "de": {"priority": 5, "label": "Germany"},
        "es": {"priority": 6, "label": "Spain"},
        "it": {"priority": 7, "label": "Italy"},
        "mx": {"priority": 8, "label": "Mexico"},
        "br": {"priority": 9, "label": "Brazil"},
        "ar": {"priority": 10, "label": "Argentina"},
        "cl": {"priority": 11, "label": "Chile"},
        "no": {"priority": 12, "label": "Norway"},
        "se": {"priority": 13, "label": "Sweden"},
        "dk": {"priority": 14, "label": "Denmark"},
    }

    files = [
        f
        for f in glob.glob("pluto_*.m3u")
        if "all.m3u" not in f and "master.m3u" not in f
    ]

    sorted_files = sorted(
        files,
        key=lambda x: sort_config.get(
            x.replace("pluto_", "").replace(".m3u", ""),
            {},
        ).get("priority", 99),
    )

    master_content = (
        '#EXTM3U url-tvg="https://github.com/'
        'matthuisman/i.mjh.nz/raw/master/PlutoTV/all.xml.gz"\n'
    )

    for file in sorted_files:
        region_key = file.replace("pluto_", "").replace(".m3u", "")

        country_label = sort_config.get(
            region_key,
            {"label": region_key.upper()},
        )["label"]

        if os.path.exists(file):
            with open(file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#EXTINF"):
                        # Replace category names with country names in All.
                        line = re.sub(
                            r'group-title="[^"]*"',
                            f'group-title="{country_label}"',
                            line,
                        )
                        master_content += line

                    elif not line.startswith("#EXTM3U") and line.strip():
                        master_content += line

    with open("pluto_all.m3u", "w", encoding="utf-8") as f:
        f.write(master_content)

    print(
        f"Master playlist generated from {len(sorted_files)} regional files."
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--merge":
        merge_master_playlist()

    else:
        provider = PlutoProvider()
        channels = provider.get_channels()

        output_file = f"pluto_{provider.region}.m3u"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(provider.generate_m3u(channels))

        print(
            f"[{provider.region.upper()}] "
            f"Wrote {len(channels)} channels to {output_file}"
        )

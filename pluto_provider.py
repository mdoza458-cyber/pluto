import requests
import uuid
import os
import sys
import re
from datetime import datetime
from typing import List, Dict, Any

# The only regions this workflow should generate.
REGIONS = {
    "us": {"priority": 1, "label": "United States"},
    "ca": {"priority": 2, "label": "Canada"},
    "gb": {"priority": 3, "label": "United Kingdom"},
    "de": {"priority": 4, "label": "Germany"},
    "mx": {"priority": 5, "label": "Mexico"},
    "no": {"priority": 6, "label": "Norway"},
    "se": {"priority": 7, "label": "Sweden"},
    "dk": {"priority": 8, "label": "Denmark"},
}

class BaseProvider:
    def __init__(self, name):
        self.name = name

    def get_user_agent(self):
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        )

    def get_timeout(self):
        return 30

class PlutoProvider(BaseProvider):
    """Pluto TV provider with regional playlists and categories."""

    def __init__(self):
        super().__init__("pluto")

        self.device_id = str(uuid.uuid1())
        self.session_token = None
        self.stitcher_params = ""
        self.session_expires_at = 0

        self.region = os.getenv("PLUTO_REGION", "us").lower()

        if self.region not in REGIONS:
            raise ValueError(
                f"Unsupported region: {self.region}. "
                f"Enabled regions: {', '.join(REGIONS)}"
            )

        # Regional forwarding IPs retained from your working version.
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
                f"[{self.region.upper()}] Session error: {exc}",
                file=sys.stderr,
            )
            return ""

    def _get_categories(self, headers: dict) -> dict:
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
                category_name = category.get("name", "General")
                for channel_id in category.get("channelIDs", []):
                    cat_map[channel_id] = category_name

            return cat_map

        except Exception as exc:
            print(
                f"[{self.region.upper()}] Category error: {exc}",
                file=sys.stderr,
            )
            return {}

    def get_channels(self) -> List[Dict[str, Any]]:
        token = self._get_session_token()

        if not token:
            raise RuntimeError(
                f"No Pluto session token for region {self.region}"
            )

        url = (
            "https://service-channels.clusters.pluto.tv/"
            "v2/guide/channels"
        )

        headers = self.headers.copy()
        headers["authorization"] = f"Bearer {token}"

        try:
            response = requests.get(
                url,
                params={"limit": "1000"},
                headers=headers,
                timeout=self.get_timeout(),
            )
            response.raise_for_status()

            channel_data = response.json().get("data", [])

        except Exception as exc:
            raise RuntimeError(
                f"[{self.region.upper()}] Channel request failed: {exc}"
            ) from exc

        print(
            f"[{self.region.upper()}] API returned "
            f"{len(channel_data)} channels."
        )

        categories = self._get_categories(headers)
        processed_channels = []

        for channel in channel_data:
            channel_id = channel.get("id")
            name = channel.get("name")

            if not channel_id or not name:
                continue

            logo = next(
                (
                    image.get("url")
                    for image in channel.get("images", [])
                    if image.get("type") == "colorLogoPNG"
                ),
                "",
            )

            group = categories.get(channel_id, "Pluto TV")

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

            processed_channels.append({
                "id": str(channel_id),
                "name": name,
                "stream_url": stream_url,
                "logo": logo,
                "group": group,
            })

        if not processed_channels:
            raise RuntimeError(
                f"[{self.region.upper()}] No channels were generated; "
                "refusing to overwrite the regional playlist."
            )

        print(
            f"[{self.region.upper()}] Processed "
            f"{len(processed_channels)} channels."
        )

        return processed_channels

    def generate_m3u(self, channels):
        m3u = (
            '#EXTM3U url-tvg="https://github.com/'
            'matthuisman/i.mjh.nz/raw/master/PlutoTV/'
            f'{self.region}.xml.gz"\n'
        )

        for channel in channels:
            m3u += (
                f'#EXTINF:-1 tvg-id="{channel["id"]}" '
                f'tvg-logo="{channel["logo"]}" '
                f'group-title="{channel["group"]}",'
                f'{channel["name"]}\n'
            )
            m3u += f'{channel["stream_url"]}\n'

        return m3u

def merge_master_playlist():
    """Merge only enabled regional playlists into pluto_all.m3u."""

    master_content = (
        '#EXTM3U url-tvg="https://github.com/'
        'matthuisman/i.mjh.nz/raw/master/PlutoTV/all.xml.gz"\n'
    )

    total_channels = 0

    for region, config in sorted(
        REGIONS.items(),
        key=lambda item: item[1]["priority"],
    ):
        filename = f"pluto_{region}.m3u"

        if not os.path.exists(filename):
            raise FileNotFoundError(
                f"Missing regional playlist: {filename}. "
                "The merge has been stopped to avoid an incomplete master."
            )

        with open(filename, "r", encoding="utf-8") as playlist:
            lines = playlist.readlines()

        if len(lines) <= 1:
            raise RuntimeError(
                f"{filename} is empty or has no channels."
            )

        for line in lines:
            if line.startswith("#EXTINF"):
                line = re.sub(
                    r'group-title="[^"]*"',
                    f'group-title="{config["label"]}"',
                    line,
                )
                master_content += line
                total_channels += 1

            elif not line.startswith("#EXTM3U") and line.strip():
                master_content += line

    temp_file = "pluto_all.m3u.tmp"

    with open(temp_file, "w", encoding="utf-8") as output:
        output.write(master_content)

    os.replace(temp_file, "pluto_all.m3u")

    print(
        f"Master playlist generated with {total_channels} entries "
        f"from {len(REGIONS)} enabled regions."
    )

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--merge":
        merge_master_playlist()

    else:
        provider = PlutoProvider()
        channels = provider.get_channels()

        output_file = f"pluto_{provider.region}.m3u"
        temp_file = output_file + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as output:
            output.write(provider.generate_m3u(channels))

        os.replace(temp_file, output_file)

        print(
            f"[{provider.region.upper()}] Wrote "
            f"{len(channels)} channels to {output_file}"
        )
```

## One small workflow change is also needed

Your existing YAML removes the eight enabled playlists before regenerating them, but it doesn't remove old regional files for countries you no longer use.

In the Update Regional Playlists step, add this cleanup before the loop:

bash
```
# Remove stale regional playlists not in the enabled region list.
for file in pluto_*.m3u; do
  [ -e "$file" ] || continue

  case "$file" in
    pluto_us.m3u|pluto_ca.m3u|pluto_gb.m3u|pluto_de.m3u|pluto_mx.m3u|pluto_no.m3u|pluto_se.m3u|pluto_dk.m3u|pluto_all.m3u)
      ;;
    *)
      echo "Removing disabled regional playlist: $file"
      rm -f "$file"
      ;;
  esac
done

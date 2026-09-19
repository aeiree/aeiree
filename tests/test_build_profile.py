from __future__ import annotations

import base64
import io
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime as RealDateTime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import yaml
from PIL import Image, ImageDraw

from scripts import build_profile


class FrozenDateTime(RealDateTime):
    frozen_utc = RealDateTime(2026, 9, 1, 17, 20, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz: ZoneInfo | None = None) -> "FrozenDateTime":
        return cls.fromtimestamp(cls.frozen_utc.timestamp(), tz=tz)


def copyable_card(markdown: str) -> str:
    return markdown.split("```text\n", 1)[1].split("\n```", 1)[0]


class RefreshScheduleTests(unittest.TestCase):
    def test_next_refresh_defaults_to_the_twelve_hour_slot_on_the_hour(self) -> None:
        FrozenDateTime.frozen_utc = RealDateTime(2026, 9, 1, 17, 20, tzinfo=timezone.utc)
        with patch.object(build_profile, "datetime", FrozenDateTime):
            eta, scheduled_at = build_profile._next_refresh(ZoneInfo("America/New_York"))

        self.assertEqual("10h 40m", eta)
        self.assertEqual("2026-09-02 00:00 EDT", scheduled_at)

    def test_next_refresh_converts_new_york_schedule_into_profile_timezone(self) -> None:
        FrozenDateTime.frozen_utc = RealDateTime(2026, 9, 1, 12, 20, tzinfo=timezone.utc)
        with patch.object(build_profile, "datetime", FrozenDateTime):
            eta, scheduled_at = build_profile._next_refresh(ZoneInfo("Europe/London"))

        self.assertEqual("3h 40m", eta)
        self.assertEqual("2026-09-01 17:00 BST", scheduled_at)

    def test_next_refresh_eta_uses_elapsed_time_across_spring_dst_change(self) -> None:
        FrozenDateTime.frozen_utc = RealDateTime(2027, 3, 14, 5, 18, tzinfo=timezone.utc)
        with patch.object(build_profile, "datetime", FrozenDateTime):
            eta, scheduled_at = build_profile._next_refresh(ZoneInfo("America/New_York"))

        self.assertEqual("10h 42m", eta)
        self.assertEqual("2027-03-14 12:00 EDT", scheduled_at)

    def test_workflow_schedule_matches_generator_defaults(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load(
            (repository_root / ".github/workflows/refresh-profile.yml").read_text(encoding="utf-8")
        )

        self.assertEqual(
            [
                {
                    "cron": "0 */12 * * *",
                    "timezone": "America/New_York",
                }
            ],
            workflow["on"]["schedule"],
        )

    def test_workflow_runs_tests_before_default_branch_asset_write(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load(
            (repository_root / ".github/workflows/refresh-profile.yml").read_text(encoding="utf-8")
        )

        self.assertIn("pull_request", workflow["on"])
        self.assertIn("test", workflow["jobs"])
        self.assertEqual("test", workflow["jobs"]["build"]["needs"])
        self.assertIn("github.event_name != 'pull_request'", workflow["jobs"]["build"]["if"])


def profile_fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    profile = {
        "login": "aeiree",
        "name": "Eri Reilly",
        "bio": "SWE and Professional Cool Kid",
        "blog": "https://aeiree.com/",
        "created_at": "2004-07-13T00:00:00Z",
    }
    stats = {
        "repo_count": 1,
        "commits": 315,
        "additions": 2190,
        "deletions": 327,
        "lines_of_code": 2629,
        "source": "GitHub GraphQL",
    }
    config = {
        "profile": {
            "card_title": "Eri Reilly",
            "role": "Software Engineer",
            "discord": "@aeiree",
            "email": "",
            "additional_fields": {
                "focus": "Full-Stack Systems · Applied AI · Infrastructure",
                "education": "Rutgers University — CS + Marketing",
            },
            "show_website": True,
        },
        "sections": {
            "stack": {
                "languages": "TypeScript · Python · JavaScript · SQL",
                "frontend": "React · React Native · Vite · Capacitor · Next.js",
                "backend": "Node.js · PostgreSQL · Supabase · PostGIS · SQLite",
                "ai": "RAG · Open WebUI · LLM Tool Calling · NLP",
                "infra": "Docker · Kubernetes · gVisor · GitHub Actions · UniFi",
                "testing": "Jest · Supertest · Playwright · Appium",
                "platforms": "SAP · Firebase · GitHub · GitLab · Jira · New Relic",
            },
            "current systems": {
                "WOE": "SAP-connected ordering · web / iOS / Android",
                "Wilbur": "Internal RAG assistant · secure AI workbench",
                "ATP": "Geospatial social platform · React Native / PostGIS",
            },
        },
        "uptime": {
            "source": "custom",
            "start_date": "2004-07-13",
            "timezone": "America/New_York",
        },
        "display": {},
    }
    return profile, stats, config


class ReadmeRenderingTests(unittest.TestCase):
    def test_readme_uses_one_dark_image_with_a_text_only_copyable_fallback(self) -> None:
        profile, stats, config = profile_fixture()

        markdown = build_profile.render_readme(profile, stats, config)
        fallback = copyable_card(markdown)

        self.assertTrue(markdown.startswith('<img alt="@aeiree profile card"'))
        self.assertIn("./assets/profile-terminal-dark.svg", markdown)
        self.assertNotIn("<picture>", markdown)
        self.assertNotIn("profile-terminal-light.svg", markdown)
        self.assertIn("<summary>copyable text version</summary>", markdown)
        self.assertEqual(fallback, fallback.lower())
        self.assertNotRegex(fallback, "[\\u2800-\\u28ff]")
        self.assertNotIn("   +", fallback)
        self.assertIn("next scheduled slot = ", fallback)

    def test_profile_card_text_is_lowercase(self) -> None:
        profile, stats, config = profile_fixture()

        markdown = build_profile.render_readme(profile, stats, config)
        fallback = copyable_card(markdown)

        self.assertIn("[eri reilly]", fallback)
        self.assertIn("focus = full-stack systems · applied ai · infrastructure", fallback)
        self.assertIn("education = rutgers university — cs + marketing", fallback)
        self.assertIn("website = aeiree.com", fallback)
        self.assertIn("frontend = react · react native · vite · capacitor · next.js", fallback)
        self.assertIn("woe = sap-connected ordering · web / ios / android", fallback)
        self.assertNotIn("email =", fallback)


class AvatarRenderingTests(unittest.TestCase):
    def test_card_palette_adapts_to_the_avatar_colors(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load(
            (repository_root / "profile.template.yml").read_text(encoding="utf-8")
        )

        self.assertEqual("avatar", config["theme"]["palette"])

        blue_avatar = Image.new("RGB", (100, 100), "#243b67")
        ImageDraw.Draw(blue_avatar).rectangle((55, 0, 99, 99), fill="#9eb9e8")
        orange_avatar = Image.new("RGB", (100, 100), "#6f2f12")
        ImageDraw.Draw(orange_avatar).rectangle((55, 0, 99, 99), fill="#f4ad45")

        blue_palette = build_profile.resolve_card_palette(blue_avatar, config)
        orange_palette = build_profile.resolve_card_palette(orange_avatar, config)

        self.assertEqual("avatar", blue_palette.source)
        self.assertEqual("avatar", orange_palette.source)
        self.assertNotEqual(blue_palette, orange_palette)
        self.assertGreater(
            build_profile.color_distance(
                build_profile.hex_to_rgb(blue_palette.accent),
                build_profile.hex_to_rgb(orange_palette.accent),
            ),
            0.2,
        )
        blue_theme, _blue_accent, _blue_accent_2 = build_profile.adapt_theme(
            build_profile.DARK, blue_palette
        )
        orange_theme, _orange_accent, _orange_accent_2 = build_profile.adapt_theme(
            build_profile.DARK, orange_palette
        )
        self.assertNotEqual(blue_theme.panel, orange_theme.panel)
        self.assertNotEqual(blue_theme.border, orange_theme.border)

        profile, stats, _fixture_config = profile_fixture()
        avatar_uri = build_profile.avatar_data_uri(orange_avatar)
        dark_svg = build_profile.render_svg(
            build_profile.DARK, profile, stats, config, avatar_uri, orange_palette
        )

        self.assertIn('data-palette-source="avatar"', dark_svg)
        self.assertIn(f'data-avatar-accent="{orange_palette.accent}"', dark_svg)
        self.assertIn(f'data-avatar-accent-2="{orange_palette.accent_2}"', dark_svg)

    def test_avatar_palette_ignores_tiny_color_speckles(self) -> None:
        avatar = Image.new("RGB", (100, 100), "#33445f")
        draw = ImageDraw.Draw(avatar)
        draw.rectangle((50, 0, 99, 99), fill="#a8b8d3")
        draw.point((2, 2), fill="#ff0000")
        draw.point((97, 2), fill="#00ff00")
        draw.point((2, 97), fill="#0000ff")

        palette = build_profile.avatar_palette(avatar)

        for color in (palette.accent, palette.accent_2):
            red, green, blue = build_profile.hex_to_rgb(color)
            self.assertLess(max(red, green, blue) - min(red, green, blue), 150)

    def test_adaptive_palette_keeps_text_accents_readable(self) -> None:
        for fill in ("#050505", "#f8f8f8", "#ff00aa", "#00d85a"):
            palette = build_profile.avatar_palette(Image.new("RGB", (32, 32), fill))
            dark_theme, dark_accent, dark_accent_2 = build_profile.adapt_theme(
                build_profile.DARK, palette
            )

            self.assertGreaterEqual(
                build_profile.contrast_ratio(
                    build_profile.hex_to_rgb(dark_accent),
                    build_profile.hex_to_rgb(dark_theme.panel),
                ),
                4.5,
            )
            self.assertGreaterEqual(
                build_profile.contrast_ratio(
                    build_profile.hex_to_rgb(dark_accent_2),
                    build_profile.hex_to_rgb(dark_theme.panel),
                ),
                4.5,
            )

    def test_fixed_palette_remains_available_as_an_opt_out(self) -> None:
        config = {"theme": {"palette": "fixed", "accent": "#cc4400", "accent_2": "#0066bb"}}

        palette = build_profile.resolve_card_palette(
            Image.new("RGB", (32, 32), "#00ff00"), config
        )

        self.assertEqual("fixed", palette.source)
        self.assertEqual("#cc4400", palette.accent)
        self.assertEqual("#0066bb", palette.accent_2)

    def test_generator_embeds_the_full_color_avatar_in_the_svg(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            avatar_path = temporary_root / "avatar.png"
            avatar = Image.new("RGB", (8, 8), (255, 0, 0))
            ImageDraw.Draw(avatar).rectangle((4, 0, 7, 7), fill=(0, 0, 255))
            avatar.save(avatar_path)

            subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "scripts/build_profile.py"),
                    "--config",
                    str(repository_root / "profile.template.yml"),
                    "--output-dir",
                    str(temporary_root / "assets"),
                    "--username",
                    "aeiree",
                    "--avatar",
                    str(avatar_path),
                    "--offline",
                ],
                cwd=temporary_root,
                check=True,
                capture_output=True,
                text=True,
            )

            svg = (temporary_root / "assets/profile-terminal-dark.svg").read_text(
                encoding="utf-8"
            )
            match = re.search(r'href="data:image/png;base64,([^"]+)"', svg)

            self.assertIsNotNone(match)
            embedded = Image.open(io.BytesIO(base64.b64decode(match.group(1))))
            self.assertEqual((255, 0, 0), embedded.getpixel((0, 0))[:3])
            self.assertEqual((0, 0, 255), embedded.getpixel((7, 0))[:3])
            self.assertNotIn('class="ascii"', svg)
            self.assertFalse((temporary_root / "assets/avatar-ascii.txt").exists())
            self.assertFalse((temporary_root / "assets/profile-terminal-light.svg").exists())

    def test_svg_gives_the_expanded_profile_and_portrait_room_to_render(self) -> None:
        profile, stats, config = profile_fixture()
        avatar_uri = build_profile.avatar_data_uri(Image.new("RGB", (8, 8), "red"))

        svg = build_profile.render_svg(build_profile.DARK, profile, stats, config, avatar_uri)
        root = ET.fromstring(svg)
        namespace = "{http://www.w3.org/2000/svg}"
        portrait_panel = next(
            node
            for node in root.iter(f"{namespace}rect")
            if node.attrib.get("x") == "38" and node.attrib.get("y") == "92"
        )
        portrait = next(
            node
            for node in root.iter(f"{namespace}image")
            if node.attrib.get("href", "").startswith("data:image/png;base64,")
        )

        self.assertGreaterEqual(float(root.attrib["width"]), 1500)
        self.assertGreaterEqual(float(portrait_panel.attrib["width"]), 480)
        self.assertGreaterEqual(float(portrait.attrib["width"]), 420)
        self.assertEqual("xMidYMid slice", portrait.attrib["preserveAspectRatio"])
        visible_text = "".join(
            "".join(node.itertext())
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] in {"title", "desc", "text"}
        )
        self.assertEqual(visible_text, visible_text.lower())


if __name__ == "__main__":
    unittest.main()

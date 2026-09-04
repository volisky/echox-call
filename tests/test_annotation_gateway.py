from __future__ import annotations

import unittest
from urllib.parse import parse_qs

from echox_call.annotation_gateway.app import (
    _normalize_prefix,
    _has_unsafe_path_segment,
    _rewrite_html,
    _rewrite_location,
    _rewrite_login_body,
    _target_path_for_suffix,
)


class AnnotationGatewayTests(unittest.TestCase):
    def test_target_path_maps_only_annotation_routes(self) -> None:
        self.assertEqual(_target_path_for_suffix(""), "/console/annotations")
        self.assertEqual(_target_path_for_suffix("/upload"), "/console/annotations/upload")
        self.assertEqual(_target_path_for_suffix("/abc/audio"), "/console/annotations/abc/audio")
        self.assertEqual(
            _target_path_for_suffix("/static/console.css"),
            "/console/static/console.css",
        )
        self.assertEqual(_target_path_for_suffix("/login"), "/console/login")

    def test_html_links_are_rewritten_to_gateway_prefix(self) -> None:
        html = (
            '<link href="/console/static/console.css">'
            '<a href="/console/annotations/abc">标注</a>'
            '<a href="/console/logout">退出</a></head>'
        )
        rewritten = _rewrite_html(html, "/landa/web/biaozhu")

        self.assertIn('href="/landa/web/biaozhu/static/console.css"', rewritten)
        self.assertIn('href="/landa/web/biaozhu/abc"', rewritten)
        self.assertIn('href="/landa/web/biaozhu/logout"', rewritten)
        self.assertIn(".sidebar-nav .nav-item:not(.is-active)", rewritten)

    def test_redirect_location_is_rewritten(self) -> None:
        self.assertEqual(
            _rewrite_location(
                "/console/annotations/abc?saved=1",
                "/landa/web/biaozhu",
                "http://console:8001",
            ),
            "/landa/web/biaozhu/abc?saved=1",
        )
        self.assertEqual(
            _rewrite_location(
                "/console/login?next=%2Fconsole%2Fannotations",
                "/landa/web/biaozhu",
                "http://console:8001",
            ),
            "/landa/web/biaozhu/login?next=%2Fconsole%2Fannotations",
        )

    def test_login_next_path_is_translated_back_for_console(self) -> None:
        body = b"username=admin&password=secret&next=%2Flanda%2Fweb%2Fbiaozhu%2Fabc"
        rewritten = parse_qs(_rewrite_login_body(body, "/landa/web/biaozhu").decode("utf-8"))

        self.assertEqual(rewritten["next"], ["/console/annotations/abc"])
        self.assertEqual(rewritten["username"], ["admin"])

    def test_prefix_normalization(self) -> None:
        self.assertEqual(_normalize_prefix("landa/web/biaozhu/"), "/landa/web/biaozhu")
        with self.assertRaises(ValueError):
            _normalize_prefix("/")

    def test_parent_path_segments_are_rejected(self) -> None:
        self.assertTrue(_has_unsafe_path_segment("/static/../jobs"))
        self.assertTrue(_has_unsafe_path_segment("/static/%252e%252e/jobs"))
        self.assertFalse(_has_unsafe_path_segment("/static/console.css"))


if __name__ == "__main__":
    unittest.main()

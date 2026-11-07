import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import software_manager as sm


class CoreTests(unittest.TestCase):
    def test_parse_pacman_qi(self):
        text = """Name            : demo
Version         : 1.2.3-1
Depends On      : glibc  libfoo
Optional Deps   : extra: optional feature
Required By     : desktop-app
Install Reason  : Explicitly installed

Name            : libdemo
Version         : 2.0-1
Depends On      : None
Install Reason  : Installed as a dependency for another package

"""
        parsed = sm.parse_pacman_qi(text)
        self.assertEqual(parsed["demo"]["Version"], "1.2.3-1")
        self.assertEqual(sm.split_list(parsed["demo"]["Depends On"]), ["glibc", "libfoo"])
        self.assertEqual(sm.split_list(parsed["libdemo"]["Depends On"]), [])
        self.assertIn("dependency", parsed["libdemo"]["Install Reason"])

    def test_parse_rpm_query(self):
        text = "bash\t5.2.037-1\tBourne shell\t1700000000\t9000000\n"
        parsed = sm.parse_rpm_query(text)
        self.assertEqual(parsed[0]["name"], "bash")
        self.assertEqual(parsed[0]["version"], "5.2.037-1")
        self.assertEqual(parsed[0]["size"], 9000000)

    def test_split_package_version(self):
        self.assertEqual(sm.split_package_version("bash-5.2.37-r1"), ("bash", "5.2.37-r1"))
        self.assertEqual(sm.split_package_version("my-app-1.2.3-4"), ("my-app", "1.2.3-4"))

    def test_distro_family(self):
        family = sm.distro_family({"ID": "ubuntu", "ID_LIKE": "debian", "NAME": "Ubuntu"})
        self.assertIn("ubuntu", family)
        self.assertIn("debian", family)

    def test_node_modules_scanner_reads_manifest_without_npm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "node_modules"
            pkg = root / "demo-pkg"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text('{"name":"demo-pkg","version":"2.1.0","description":"demo","dependencies":{"left-pad":"1.0.0"}}')
            items = sm._node_modules_packages(root, "npm", "Node.js · npm global")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "demo-pkg")
            self.assertEqual(items[0].version, "2.1.0")
            self.assertEqual(items[0].dependencies, ["left-pad"])

    def test_uninstall_plans_for_native_managers(self):
        cases = {
            "apt": "sudo apt remove --purge demo",
            "dnf": "sudo dnf remove demo",
            "yum": "sudo yum remove demo",
            "zypper": "sudo zypper remove demo",
            "apk": "sudo apk del demo",
            "pacman": "sudo pacman -Rns -- demo",
        }
        for manager, expected in cases.items():
            sw = sm.Software(
                id=f"{manager}:demo", name="demo", method=manager,
                method_title=manager, package_name="demo",
                metadata={"manager": manager},
            )
            self.assertIn(expected, sm.uninstall_plan(sw))

    def test_uninstall_plans_for_language_managers(self):
        cases = {
            "pip": "-m pip uninstall demo",
            "npm": "npm uninstall -g demo",
            "cargo": "cargo uninstall demo",
            "gem": "gem uninstall demo",
        }
        for manager, expected in cases.items():
            sw = sm.Software(
                id=f"{manager}:demo", name="demo", method=manager,
                method_title=manager, package_name="demo",
                metadata={"manager": manager},
            )
            self.assertTrue(any(expected in line for line in sm.uninstall_plan(sw)))

    def test_wrap_display_counts_cjk_as_two_columns(self):
        lines = sm.wrap_display("中文abc测试", 6)
        self.assertTrue(lines)
        self.assertTrue(all(sm.display_width(line) <= 6 for line in lines))

    def test_alias_matching_rejects_short_accidental_substrings(self):
        self.assertFalse(sm.alias_matches("wemeetapp", {"app", "spice"}))
        self.assertTrue(sm.alias_matches("Kingsoft", {"kingsoft", "wps"}))
        self.assertTrue(sm.alias_matches("WPS-Office", {"wpsoffice"}))

    def test_cleanup_does_not_remove_package_body(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = root / "body"
            data = root / "data"
            body.mkdir()
            data.mkdir()
            sw = sm.Software(
                id="pacman:demo", name="demo", method="pacman",
                method_title="pacman", package_name="demo",
                artifacts=[sm.Artifact("包文件", str(body))],
                data_artifacts=[sm.Artifact("用户数据", str(data))],
            )
            clean = sm.cleanup_paths(sw)
            self.assertEqual([a.path for a in clean], [str(data)])

    def test_residual_delete_removes_only_listed_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            removable = root / "removed-app"
            keep = root / "keep"
            removable.mkdir()
            keep.mkdir()
            (removable / "cache.bin").write_text("x")
            sw = sm.Software(
                id="residual:x", name="removed-app", method="residual",
                method_title="残留", status="residual",
                data_artifacts=[sm.Artifact("用户数据", str(removable))],
            )
            deleted, errors = sm.delete_cleanup_paths(sw)
            self.assertEqual(errors, [])
            self.assertEqual(deleted, [str(removable)])
            self.assertFalse(removable.exists())
            self.assertTrue(keep.exists())

    def test_detail_has_dependency_and_location_sections(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "demo-data"
            data.mkdir()
            inv = sm.Inventory("now", "host", "Linux", td, [])
            sw = sm.Software(
                id="pacman:demo", name="demo", method="pacman",
                method_title="pacman", version="1.0", package_name="demo",
                dependencies=["glibc"], data_artifacts=[sm.Artifact("用户数据", str(data))],
                metadata={"manager": "pacman"},
            )
            lines = sm.detail_lines(sw, inv)
            text = "\n".join(x[0] for x in lines)
            self.assertIn("依赖关系", text)
            self.assertIn("安装位置", text)
            self.assertIn("glibc", text)

    def test_residual_is_marked_danger(self):
        inv = sm.Inventory("now", "host", "Linux", "/home/test", [])
        sw = sm.Software(
            id="residual:x", name="removed-app", method="residual",
            method_title="残留", status="residual",
            data_artifacts=[sm.Artifact("用户数据", "/tmp/removed-app")],
        )
        lines = sm.detail_lines(sw, inv)
        self.assertEqual(lines[0][1], "danger")
        self.assertIn("疑似残留", lines[0][0])


if __name__ == "__main__":
    unittest.main()

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

    def test_status_and_reason_explanations(self):
        unregistered = sm.Software(
            id="manual:x", name="ccls", method="source", method_title="源码 / 手动安装",
            status="unregistered", summary="source directory",
        )
        self.assertEqual(unregistered.status_label, "未发现入口")
        self.assertIn(".desktop", sm.install_reason_explanation(unregistered))

        dependency = sm.Software(
            id="pacman:a52dec", name="a52dec", method="pacman", method_title="pacman",
            package_name="a52dec", metadata={"manager": "pacman", "explicit": False},
        )
        self.assertIn("依赖", sm.software_kind(dependency))
        self.assertIn("依赖项", sm.install_reason_explanation(dependency))

    def test_reverse_dependencies_uses_pacman_required_by(self):
        sw = sm.Software(
            id="pacman:libfoo", name="libfoo", method="pacman", method_title="pacman",
            package_name="libfoo", required_by=["player", "editor"],
            metadata={"manager": "pacman"},
        )
        self.assertEqual(sm.reverse_dependencies(sw), ["player", "editor"])

    def test_global_fuzzy_search_supports_prefix_and_abbreviation(self):
        self.assertGreater(sm.fuzzy_score("zot", "Zotero"), 0)
        self.assertGreater(sm.fuzzy_score("ztr", "Zotero"), 0)
        self.assertEqual(sm.fuzzy_score("zzzz", "Zotero"), 0)

        zotero = sm.Software(
            id="manual:zotero", name="Zotero", method="source",
            method_title="源码 / 手动安装", status="installed",
        )
        python = sm.Software(
            id="apt:python3", name="python3", method="apt",
            method_title="系统包管理器 · apt/dpkg", status="installed",
        )
        missing = sm.Software(
            id="residual:zotero-old", name="Zotero Old Data", method="residual",
            method_title="残留", status="residual",
        )
        inv = sm.Inventory("now", "host", "Linux", "/home/test", [sm.Method("all", "All", "", [python, zotero, missing])])
        results = sm.search_software(inv, "zot")
        self.assertEqual(results[0].name, "Zotero")
        self.assertIn("Zotero Old Data", [item.name for item in results])
        self.assertEqual(sm.search_software(inv, ""), [])

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

    def test_vim_coc_data_is_classified_as_plugin_not_plain_residual(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cfg = home / ".vim/vimconfigs/plugins.vim"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("Plug 'neoclide/coc.nvim', {'branch': 'release'}\n")
            settings = home / ".vim/coc-settings.json"
            settings.write_text("{}")
            data = home / ".config/coc/extensions"
            data.mkdir(parents=True)
            (data / "package.json").write_text('{"dependencies":{"coc-json":">=1.0"}}')
            plugins = sm.collect_vim_plugins(home)
            coc = next(p for p in plugins if p.name == "coc.nvim")
            self.assertEqual(coc.status, "plugin_body_missing")
            self.assertEqual(coc.method, "vim-plugin")
            self.assertTrue(any(a.path == str(home / ".config/coc") for a in coc.data_artifacts))
            self.assertIn("coc-json", coc.metadata["sub_extensions"])

    def test_vscode_extension_and_global_storage_are_linked(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ext = home / ".vscode/extensions/ms-python.python-1.0.0"
            ext.mkdir(parents=True)
            (ext / "package.json").write_text('{"publisher":"ms-python","name":"python","displayName":"Python","version":"1.0.0","description":"Python support"}')
            storage = home / ".config/Code/User/globalStorage/ms-python.python"
            storage.mkdir(parents=True)
            plugins = sm.collect_vscode_extensions(home)
            python = next(p for p in plugins if p.package_name == "ms-python.python")
            self.assertEqual(python.status, "installed")
            self.assertEqual(python.version, "1.0.0")
            self.assertTrue(any(a.path == str(storage) for a in python.data_artifacts))

    def test_chromium_extension_manifest_and_storage_are_linked(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ext = home / ".config/chromium/Default/Extensions/abcdefghijklmnop/1.2.0"
            ext.mkdir(parents=True)
            (ext / "manifest.json").write_text('{"name":"__MSG_name__","description":"Demo extension","version":"1.2.0","default_locale":"en"}')
            locale = ext / "_locales/en"
            locale.mkdir(parents=True)
            (locale / "messages.json").write_text('{"name":{"message":"Demo Browser Extension"}}')
            storage = home / ".config/chromium/Default/Local Extension Settings/abcdefghijklmnop"
            storage.mkdir(parents=True)
            plugins = sm.collect_browser_extensions(home)
            chrome = next(p for p in plugins if p.package_name == "abcdefghijklmnop")
            self.assertEqual(chrome.name, "Demo Browser Extension")
            self.assertTrue(any(a.path == str(storage) for a in chrome.data_artifacts))

    def test_plugin_reverse_dependencies(self):
        base = sm.Software(
            id="vscode-extension:vendor.base", name="Base", method="vscode-extension",
            method_title="VS Code 扩展", package_name="vendor.base",
            metadata={"plugin_type": "VS Code 扩展"},
        )
        dependent = sm.Software(
            id="vscode-extension:vendor.extra", name="Extra", method="vscode-extension",
            method_title="VS Code 扩展", package_name="vendor.extra",
            dependencies=["vendor.base"], metadata={"plugin_type": "VS Code 扩展"},
        )
        inv = sm.Inventory("now", "host", "Linux", "/home/test", [sm.Method("editor", "Editor", "", [base, dependent])])
        self.assertEqual(sm.plugin_reverse_dependencies(base, inv), ["Extra"])

    def test_flash_is_browser_plugin_not_source_directory(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            flash = home / ".application/flashplayer"
            flash.parent.mkdir(parents=True)
            flash.write_bytes(b"not-a-real-binary")
            plugins = sm.collect_flash_plugins(home)
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0].method, "browser-plugin")
            self.assertIn("浏览器插件", plugins[0].metadata["plugin_type"])
            self.assertTrue(any(a.path == str(flash) for a in plugins[0].artifacts))

    def test_zotero_local_opt_self_updating_app_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            zotero = home / ".local/opt/zotero"
            zotero.mkdir(parents=True)
            (zotero / "zotero").write_text("#!/bin/sh\n")
            (zotero / "app").mkdir()
            (zotero / "app/application.ini").write_text(
                "[App]\nName=Zotero\nVersion=10.0.7\nID=zotero@zotero.org\nVendor=Zotero\n"
                "[AppUpdate]\nURL=https://example.invalid/update.xml\n"
            )
            (zotero / "updates/0").mkdir(parents=True)
            launcher = home / ".local/bin/zotero"
            launcher.parent.mkdir(parents=True)
            launcher.symlink_to(zotero / "zotero")
            desktop_path = home / ".local/share/applications/zotero.desktop"
            desktop_path.parent.mkdir(parents=True)
            desktop_path.write_text("[Desktop Entry]\nName=Zotero\nComment=Reference manager\nExec=" + str(zotero / "zotero") + " -url %U\n")
            desktop = [{
                "path": str(desktop_path), "Name": "Zotero", "Comment": "Reference manager",
                "Exec": str(zotero / "zotero") + " -url %U",
            }]
            local_bins = [{"launcher": str(launcher), "target": str(zotero / "zotero")}]
            items = sm.collect_source_manual(home, desktop, local_bins)
            app = next(s for s in items if s.name == "Zotero")
            self.assertEqual(app.version, "10.0.7")
            self.assertEqual(app.summary, "Reference manager")
            self.assertEqual(app.metadata["install_root"], str(zotero.resolve()))
            self.assertTrue(app.metadata["self_updating"])
            self.assertIn("MAR", app.metadata["update_system"])
            text = "\n".join(line for line, _ in sm.detail_lines(app, sm.Inventory("now", "host", "Linux", td, [])))
            self.assertIn("Mozilla MAR", text)

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

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.vault_crypto import (
    VaultDecryptionError,
    VaultError,
    VaultKeyCorruptedError,
    VaultKeyLostError,
    VaultPassphraseError,
    _classify_vault_error,
    decrypt_escrow_recovery_blob,
    decrypt_vault_payload,
    derive_escrow_id_and_auth,
    derive_subkeys,
    derive_write_token,
    encrypt_escrow_recovery_blob,
    encrypt_vault_payload,
    export_obsidian_markdown,
    format_key_handover_notice,
    format_vault_markdown_output,
    generate_sync_key,
    get_vault_id_path,
    load_local_vault,
    parse_sync_key,
    resolve_sync_key,
    save_local_vault,
    validate_passphrase_strength,
    vault_delete_place,
    vault_export,
    vault_import,
    vault_query_places,
    vault_save_place,
    vault_status,
    vault_sync,
)


class TestVaultCrypto(unittest.TestCase):
    def test_sync_key_generation_and_parsing(self) -> None:
        for _ in range(20):
            key = generate_sync_key()
            self.assertTrue(key.startswith("tld_"))
            vault_id, master_secret = parse_sync_key(key)
            self.assertEqual(len(vault_id), 16)
            self.assertNotIn("_", vault_id)
            self.assertGreaterEqual(len(master_secret), 32)

        # Also test master_secret containing underscores
        key_with_underscores = "tld_9xKm2pQ8vR4nL7wZ_4bY1c8f92a_KLmP0qRsTu_VwXyZ1234567890abcdef"
        vid, msec = parse_sync_key(key_with_underscores)
        self.assertEqual(vid, "9xKm2pQ8vR4nL7wZ")
        self.assertEqual(msec, "4bY1c8f92a_KLmP0qRsTu_VwXyZ1234567890abcdef")

        with self.assertRaises(VaultError):
            parse_sync_key("invalid_key_format")
        with self.assertRaises(VaultError):
            parse_sync_key("tld_short_secret")

    def test_write_token_and_subkey_derivation(self) -> None:
        key = "tld_9xKm2pQ8vR4nL7wZ_4bY1c8f92aKLmP0qRsTuVwXyZ1234567890abcdefGH"
        vid, msec = parse_sync_key(key)
        t1 = derive_write_token(vid, msec)
        t2 = derive_write_token(vid, msec)
        self.assertEqual(t1, t2)
        self.assertEqual(len(t1), 64)  # hex sha256

        nonce = b"0123456789abcdef"
        enc_key, mac_key = derive_subkeys(msec, nonce)
        self.assertEqual(len(enc_key), 32)
        self.assertEqual(len(mac_key), 32)
        self.assertNotEqual(enc_key, mac_key)

    def test_e2ee_encryption_decryption_roundtrip(self) -> None:
        sync_key = generate_sync_key()
        sample_vault = {
            "schema_version": 1,
            "updated_at": "2026-10-18T14:00:00Z",
            "places": {
                "0x3442abcd1234:0x9876efgh": {
                    "place_id": "0x3442abcd1234:0x9876efgh",
                    "name": "老張牛肉麵 (Lao Zhang Beef Noodle)",
                    "address": "台北市大安區新生南路一段",
                    "lat": 25.0330,
                    "lng": 121.5654,
                    "status": "wishlist",
                    "tags": ["牛肉麵", "大安區"],
                    "must_try": ["紅燒半筋半肉麵"],
                }
            },
        }
        envelope = encrypt_vault_payload(sample_vault, sync_key)
        self.assertEqual(envelope["v"], 1)
        self.assertIn("nonce", envelope)
        self.assertIn("ct", envelope)
        self.assertIn("tag", envelope)
        self.assertIn("updated_at", envelope)

        # Ensure ciphertext does not leak plaintext strings
        raw_envelope_str = json.dumps(envelope)
        self.assertNotIn("老張牛肉麵", raw_envelope_str)
        self.assertNotIn("0x3442abcd1234", raw_envelope_str)

        decrypted = decrypt_vault_payload(envelope, sync_key)
        self.assertEqual(decrypted, sample_vault)

    def test_e2ee_tamper_detection_and_wrong_key(self) -> None:
        sync_key = generate_sync_key()
        wrong_key = generate_sync_key()
        payload = {"schema_version": 1, "places": {"p1": {"name": "一蘭拉麵"}}}
        envelope = encrypt_vault_payload(payload, sync_key)

        # 1. Wrong key must raise VaultDecryptionError
        with self.assertRaises(VaultDecryptionError):
            decrypt_vault_payload(envelope, wrong_key)

        # 2. Tamper 1 byte in ciphertext (ct)
        ct_bytes = bytearray(base64.b64decode(envelope["ct"]))
        ct_bytes[0] ^= 0xFF
        tampered_ct = dict(envelope, ct=base64.b64encode(bytes(ct_bytes)).decode("ascii"))
        with self.assertRaises(VaultDecryptionError):
            decrypt_vault_payload(tampered_ct, sync_key)

        # 3. Tamper 1 byte in nonce
        nonce_bytes = bytearray(base64.b64decode(envelope["nonce"]))
        nonce_bytes[0] ^= 0x01
        tampered_nonce = dict(envelope, nonce=base64.b64encode(bytes(nonce_bytes)).decode("ascii"))
        with self.assertRaises(VaultDecryptionError):
            decrypt_vault_payload(tampered_nonce, sync_key)

        # 4. Tamper 1 byte in tag
        tag_bytes = bytearray(base64.b64decode(envelope["tag"]))
        tag_bytes[-1] ^= 0x01
        tampered_tag = dict(envelope, tag=base64.b64encode(bytes(tag_bytes)).decode("ascii"))
        with self.assertRaises(VaultDecryptionError):
            decrypt_vault_payload(tampered_tag, sync_key)


class TestVaultStorageAndQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_path = os.path.join(self.temp_dir.name, "vault.json")
        self.key_path = os.path.join(self.temp_dir.name, "sync.key")
        self.obsidian_dir = os.path.join(self.temp_dir.name, "obsidian")
        self.env_patcher = patch.dict(
            os.environ,
            {
                "TLDREVIEW_VAULT_PATH": self.vault_path,
                "TLDREVIEW_SYNC_KEY_PATH": self.key_path,
                "TLDREVIEW_OBSIDIAN_DIR": self.obsidian_dir,
            },
            clear=False,
        )
        self.env_patcher.start()
        os.environ.pop("TLDREVIEW_SYNC_KEY", None)
        # Mock network calls so unit tests never hit external endpoints
        self.put_patcher = patch("core.tldreview_tool._cloud_vault_put", return_value=True)
        self.get_patcher = patch("core.tldreview_tool._cloud_vault_get", return_value=None)
        self.mock_put = self.put_patcher.start()
        self.mock_get = self.get_patcher.start()

    def tearDown(self) -> None:
        self.put_patcher.stop()
        self.get_patcher.stop()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_upsert_and_deduplication_wishlist_to_liked(self) -> None:
        # 1. Save as wishlist first
        res1 = vault_save_place(
            place_id="0x3442abcd1234:0x9876efgh",
            name="老張牛肉麵",
            address="台北市大安區新生南路一段",
            lat=25.0330,
            lng=121.5654,
            google_rating=4.3,
            status="wishlist",
            tags="牛肉麵,大安區",
            must_try="紅燒半筋半肉麵,花椒牛油",
            red_flags="湯頭偏鹹",
            insider_tips="只收現金",
            user_notes="下次帶爸媽去吃",
        )
        self.assertEqual(res1["action"], "created")
        self.assertTrue(res1["new_sync_key_generated"])
        self.assertIn("sync_key", res1)
        p1 = res1["place"]
        self.assertEqual(p1["status"], "wishlist")
        self.assertIsNone(p1["visited_at"])
        self.assertEqual(p1["tags"], ["牛肉麵", "大安區"])
        self.assertTrue(os.path.exists(res1["obsidian_path"]))

        # 2. Update same place to 'liked' with user_rating, overlapping + new tags/must_try, and new note
        res2 = vault_save_place(
            place_id="0x3442abcd1234:0x9876efgh",
            name="老張牛肉麵",
            status="liked",
            user_rating=4.8,
            tags="大安區,長輩友善,必訪",
            must_try="紅燒半筋半肉麵,滷牛肚",
            insider_tips="11:45前到免排隊",
            user_notes="爸媽很愛半筋半肉！",
        )
        self.assertEqual(res2["action"], "updated")
        self.assertFalse(res2["new_sync_key_generated"])
        self.assertEqual(res2["total_saved_places"], 1)
        p2 = res2["place"]
        self.assertEqual(p2["status"], "liked")
        self.assertIsNotNone(p2["visited_at"])
        self.assertEqual(p2["user_rating"], 4.8)
        self.assertEqual(p2["tags"], ["牛肉麵", "大安區", "長輩友善", "必訪"])
        self.assertEqual(p2["must_try"], ["紅燒半筋半肉麵", "花椒牛油", "滷牛肚"])
        self.assertEqual(p2["insider_tips"], ["只收現金", "11:45前到免排隊"])
        self.assertIn("下次帶爸媽去吃", p2["user_notes"])
        self.assertIn("爸媽很愛半筋半肉！", p2["user_notes"])

    def test_haversine_spatial_filtering_and_sorting(self) -> None:
        # Place A: Taipei 101 (~0.5 km from Xinyi Anhe / 25.0330, 121.5654 -> actually right at 25.0339, 121.5645)
        vault_save_place(
            place_id="place_taipei_101",
            name="鼎泰豐 101店",
            lat=25.0339,
            lng=121.5645,
            status="wishlist",
            tags="小籠包,信義區",
        )
        # Place B: Daan Forest Park (~2.8 km away: 25.0300, 121.5358)
        vault_save_place(
            place_id="place_daan",
            name="老張牛肉麵 大安店",
            lat=25.0300,
            lng=121.5358,
            status="wishlist",
            tags="牛肉麵,大安區",
        )
        # Place C: Tamsui (~20 km away: 25.1677, 121.4455)
        vault_save_place(
            place_id="place_tamsui",
            name="淡水阿給老店",
            lat=25.1677,
            lng=121.4455,
            status="wishlist",
            tags="小吃,淡水",
        )

        # Query within 5.0 km of (25.0330, 121.5654)
        q_res = vault_query_places(
            status="wishlist",
            lat=25.0330,
            lng=121.5654,
            radius_km=5.0,
        )
        self.assertEqual(q_res["total_in_vault"], 3)
        self.assertEqual(q_res["matched_count"], 2)
        names = [p["name"] for p in q_res["places"]]
        self.assertEqual(names, ["鼎泰豐 101店", "老張牛肉麵 大安店"])
        self.assertLess(q_res["places"][0]["distance_km"], q_res["places"][1]["distance_km"])

        # Query with keyword filter
        q_kw = vault_query_places(status="all", keyword="牛肉麵")
        self.assertEqual(q_kw["matched_count"], 1)
        self.assertEqual(q_kw["places"][0]["place_id"], "place_daan")

        # Delete place and verify
        del_res = vault_delete_place(name="老張牛肉麵 大安店")
        self.assertEqual(del_res["deleted_count"], 1)
        self.assertEqual(del_res["remaining_count"], 2)

    def test_vault_sync_merges_remote_and_local(self) -> None:
        vault_save_place(
            place_id="local_only",
            name="本地私房菜",
            status="liked",
        )
        self.mock_get.return_value = {
            "schema_version": 1,
            "places": {
                "remote_only": {
                    "place_id": "remote_only",
                    "name": "雲端同步拉麵店",
                    "status": "wishlist",
                    "saved_at": "2026-10-18T12:00:00Z",
                }
            },
        }
        sync_res = vault_sync()
        self.assertEqual(sync_res["total_places"], 2)
        q_all = vault_query_places(status="all")
        self.assertEqual(q_all["matched_count"], 2)

    def test_cli_vault_save_and_query(self) -> None:
        env = dict(os.environ)
        env["TLDREVIEW_VAULT_PATH"] = self.vault_path
        env["TLDREVIEW_SYNC_KEY_PATH"] = self.key_path
        env["TLDREVIEW_VAULT_API_URL"] = "http://127.0.0.1:9/offline"  # ensure fast offline fallback

        save_proc = subprocess.run(
            [
                sys.executable,
                "core/tldreview_tool.py",
                "--vault-save",
                "--name",
                "林東芳牛肉麵",
                "--status",
                "wishlist",
                "--lat",
                "25.0484",
                "--lng",
                "121.5432",
                "--tags",
                "牛肉麵,中山區",
                "--must-try",
                "半筋半肉麵,花干",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        save_data = json.loads(save_proc.stdout)
        self.assertEqual(save_data["status"], "success")
        self.assertEqual(save_data["place"]["name"], "林東芳牛肉麵")

        query_proc = subprocess.run(
            [
                sys.executable,
                "core/tldreview_tool.py",
                "--vault-query",
                "--status",
                "wishlist",
                "--lat",
                "25.0480",
                "--lng",
                "121.5430",
                "--radius-km",
                "2.0",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        query_data = json.loads(query_proc.stdout)
        self.assertEqual(query_data["matched_count"], 1)
        self.assertEqual(query_data["places"][0]["name"], "林東芳牛肉麵")

    def test_error_classification_and_explicit_warning_on_save_failure(self) -> None:
        import io
        import urllib.error

        # 1. Verify error classification distinguishes 403 auth_error vs timeout vs 502 server_error
        err_403 = urllib.error.HTTPError(
            url="https://example.com/v1/vault/abc",
            code=403,
            msg="Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"Invalid write_token"}'),
        )
        diag_403 = _classify_vault_error(err_403)
        self.assertEqual(diag_403["error_type"], "auth_error")
        self.assertFalse(diag_403["retryable"])

        diag_timeout = _classify_vault_error(TimeoutError("timed out"))
        self.assertEqual(diag_timeout["error_type"], "timeout")
        self.assertTrue(diag_timeout["retryable"])

        # 2. Verify vault_save_place surfaces explicit warning in JSON & Markdown when cloud sync fails
        self.mock_put.return_value = False
        res = vault_save_place(
            place_id="offline_test_1",
            name="測試離線餐廳",
            status="wishlist",
        )
        self.assertFalse(res["cloud_synced"])
        self.assertTrue(res["sync_pending"])
        self.assertIn("cloud_sync_error", res)
        self.assertIn("cloud_sync_hint", res)

        md = format_vault_markdown_output(res)
        self.assertIn("⚠️", md)
        self.assertIn("僅存於本地快取", md)

    def test_vault_status_diagnostics(self) -> None:
        vault_save_place(
            place_id="status_test_1",
            name="診斷測試牛肉麵",
            status="liked",
        )
        with patch.dict(os.environ, {"TLDREVIEW_VAULT_OFFLINE": "1"}, clear=False):
            st = vault_status()
        self.assertEqual(st["mode"], "vault_status")
        self.assertEqual(st["local"]["total_places"], 1)
        self.assertEqual(st["local"]["status_counts"]["liked"], 1)
        self.assertTrue(st["sync_key"]["configured"])
        md = format_vault_markdown_output(st)
        self.assertIn("--vault-status", md)
        self.assertIn("診斷", md)

    def test_passphrase_escrow_roundtrip_and_strength(self) -> None:
        # 1. Weak passphrases must be rejected
        for weak in ("12345678", "password", "short", "aaaaaaaaaa", "牛肉麵"):
            with self.assertRaises(VaultPassphraseError):
                validate_passphrase_strength(weak)

        # 2. Good Chinese & English passphrases must succeed and round-trip encrypt/decrypt sync_key
        sync_key = generate_sync_key()
        passphrase = "我阿嬤煮的紅燒牛肉麵最香"
        esc_id1, token1 = derive_escrow_id_and_auth(passphrase)
        esc_id2, token2 = derive_escrow_id_and_auth("  我阿嬤煮的紅燒牛肉麵最香  ")
        self.assertEqual(esc_id1, esc_id2)
        self.assertEqual(token1, token2)
        self.assertTrue(esc_id1.startswith("esc_"))

        blob = encrypt_escrow_recovery_blob(sync_key, passphrase)
        self.assertNotIn(sync_key, json.dumps(blob))
        recovered = decrypt_escrow_recovery_blob(blob, passphrase)
        self.assertEqual(recovered, sync_key)

        # 3. Wrong passphrase must fail HMAC verification
        with self.assertRaises(VaultDecryptionError):
            decrypt_escrow_recovery_blob(blob, "隔壁老王煮的陽春麵比較鹹")

    def test_key_loss_and_corruption_detection_prevents_silent_regeneration(self) -> None:
        # 1. Initial save creates sync.key AND vault_id
        res = vault_save_place(
            place_id="loss_test_1",
            name="永康牛肉麵",
            status="wishlist",
        )
        orig_key = res["sync_key"]
        orig_vid, _ = parse_sync_key(orig_key)
        self.assertTrue(os.path.isfile(self.key_path))
        self.assertTrue(os.path.isfile(get_vault_id_path()))

        # 2. Simulate corrupted sync.key -> must raise VaultKeyCorruptedError (NEVER silently regenerate!)
        with open(self.key_path, "w", encoding="utf-8") as f:
            f.write("corrupted_garbage_key\n")
        with self.assertRaises(VaultKeyCorruptedError):
            resolve_sync_key(auto_create=True)

        # 3. Simulate deleted sync.key while vault_id still exists -> must raise VaultKeyLostError!
        os.remove(self.key_path)
        with self.assertRaises(VaultKeyLostError):
            vault_save_place(name="另一家店", status="wishlist")

        # 4. Restoring the original key via explicit --sync-key recovers cleanly
        restored, is_new = resolve_sync_key(explicit_key=orig_key, auto_create=True)
        self.assertEqual(restored, orig_key)
        self.assertFalse(is_new)
        vid_now, _ = parse_sync_key(restored or "")
        self.assertEqual(vid_now, orig_vid)

    def test_isatty_handover_notice_and_export_import_backup(self) -> None:
        sync_key = generate_sync_key()
        # TTY mode shows full key
        tty_notice = format_key_handover_notice(sync_key, key_path=self.key_path, is_tty=True)
        self.assertIn(sync_key, tty_notice["message"])
        # Non-TTY (Agent/Pipe) mode redacts raw key and shows absolute path
        non_tty_notice = format_key_handover_notice(sync_key, key_path=self.key_path, is_tty=False)
        self.assertNotIn(sync_key, non_tty_notice["message"])
        self.assertIn(os.path.abspath(self.key_path), non_tty_notice["message"])

        # Export & Import after key loss
        vault_save_place(place_id="exp_1", name="阜杭豆漿", status="liked", must_try="厚燒餅夾蛋")
        backup_file = os.path.join(self.temp_dir.name, "backup.json")
        exp_res = vault_export(output_path=backup_file)
        self.assertEqual(exp_res["total_places"], 1)
        self.assertTrue(os.path.isfile(backup_file))

        # Simulate catastrophic VM wipe (both sync.key and vault.json gone, only backup.json saved)
        os.remove(self.key_path)
        os.remove(self.vault_path)
        imp_res = vault_import(backup_file)
        self.assertEqual(imp_res["imported_count"], 1)
        self.assertTrue(imp_res["new_sync_key_generated"])
        q_res = vault_query_places(status="all")
        self.assertEqual(q_res["matched_count"], 1)
        self.assertEqual(q_res["places"][0]["name"], "阜杭豆漿")


if __name__ == "__main__":
    unittest.main()

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
    decrypt_vault_payload,
    derive_subkeys,
    derive_write_token,
    encrypt_vault_payload,
    export_obsidian_markdown,
    format_vault_markdown_output,
    generate_sync_key,
    load_local_vault,
    parse_sync_key,
    resolve_sync_key,
    save_local_vault,
    vault_delete_place,
    vault_query_places,
    vault_save_place,
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


if __name__ == "__main__":
    unittest.main()

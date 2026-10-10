import base64
import importlib.util
import json
import os
import struct
import sys
import tempfile
import types
import unittest
import openai as real_openai
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_provider", ROOT / "__init__.py")
provider = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = provider
spec.loader.exec_module(provider)

PNG_1536x1024 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1536, 1024) + b"\x08\x06\x00\x00\x00"


def fake_addrinfo(*addresses):
    def resolve(host, port=None, *args, **kwargs):
        return [(2 if ":" not in a else 10, 1, 6, "", (a, port or 0)) for a in addresses]
    return resolve


def mock_ssrf_client(handler):
    """``create_ssrf_safe_client`` stand-in that serves ``handler`` and records the client kwargs."""
    import httpx
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return factory, seen


class FakeAPIStatusError(Exception):
    def __init__(self, status_code, body=None, text=""):
        super().__init__(text)
        self.status_code = status_code
        self.body = body
        self.response = types.SimpleNamespace(text=text)


class FakeAPIConnectionError(Exception):
    pass


class FakeAPITimeoutError(FakeAPIConnectionError):
    pass


class FakeOmit:
    pass


class FakeImage:
    b64_json = base64.b64encode(PNG_1536x1024).decode()
    url = None
    revised_prompt = None


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def iter_lines(self):
        for event in self:
            yield "data: " + json.dumps(vars(event))
            yield ""


class FakeImages:
    def __init__(self, raise_exc=None):
        self.generate_calls = []
        self.edit_calls = []
        self.raise_exc = raise_exc

    @property
    def with_streaming_response(self):
        return self

    def _respond(self, kwargs, kind):
        if self.raise_exc is not None:
            raise self.raise_exc
        if kwargs.get("stream"):
            events = FakeClient.events
            if events is None:
                events = [types.SimpleNamespace(type=kind + ".completed", b64_json=FakeImage.b64_json)]
            self.stream = FakeStream(events)
            return self.stream
        return types.SimpleNamespace(data=[FakeImage()])

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._respond(kwargs, "image_generation")

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return self._respond(kwargs, "image_edit")


class FakeClient:
    instances = []
    raise_exc: "Exception | None" = None
    events = None

    def __init__(self, *, base_url, api_key, default_headers=None, timeout=None, max_retries=None):
        self.base_url = base_url
        self.api_key = api_key
        self.default_headers = default_headers or {}
        self.images = FakeImages(self.__class__.raise_exc)
        self.timeout = timeout
        self.max_retries = max_retries
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class Ctx:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.registered = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_image_gen_provider(self, item):
        self.registered.append(item)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        FakeClient.raise_exc = None
        FakeClient.events = None
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path = Path(self.tmp.name) / "out.png"
        self.saved_path.write_bytes(PNG_1536x1024)
        self.env = patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_API_KEY": "test-key-123456",
                                           "HERMES_ALLOW_PRIVATE_URLS": "false"}, clear=True)
        self.env.start()
        fake_openai = types.SimpleNamespace(
            OpenAI=FakeClient, APIStatusError=FakeAPIStatusError, Omit=FakeOmit,
            APIConnectionError=FakeAPIConnectionError, APITimeoutError=FakeAPITimeoutError)
        self.openai = patch.dict(sys.modules, {"openai": fake_openai})
        self.openai.start()
        self.saved = patch.object(provider, "save_b64_image", return_value=self.saved_path)
        self.saved.start()

    def tearDown(self):
        self.saved.stop()
        self.openai.stop()
        self.env.stop()
        self.tmp.cleanup()

    def make(self, **settings):
        settings.setdefault("base_url", "https://gateway.example/v1")
        return provider.OpenAICompatibleImageGenProvider(Ctx(settings))

    def call(self, client_index=0, kind="generate_calls"):
        return getattr(FakeClient.instances[client_index].images, kind)[0]

    # -- configuration ---------------------------------------------------------------

    def test_settings_base_url_and_default_key_env(self):
        result = self.make().generate("draw", "landscape", model="gpt-image-2")
        self.assertTrue(result["success"], result)
        client = FakeClient.instances[0]
        self.assertEqual(client.base_url, "https://gateway.example/v1")
        self.assertEqual(client.api_key, "test-key-123456")
        self.assertEqual(client.default_headers["User-Agent"], provider.USER_AGENT)

    def test_key_env_points_at_another_variable(self):
        with patch.dict(os.environ, {"MY_GATEWAY_KEY": "other-key-abcdef"}):
            self.make(key_env="MY_GATEWAY_KEY").generate("draw", model="gpt-image-2")
        self.assertEqual(FakeClient.instances[0].api_key, "other-key-abcdef")

    def test_legacy_env_base_url_still_works(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_BASE_URL": "https://legacy.example/v1"}):
            p = provider.OpenAICompatibleImageGenProvider(Ctx())
            self.assertTrue(p.is_available())
            self.assertTrue(p.generate("draw", model="gpt-image-2")["success"])
        self.assertEqual(FakeClient.instances[0].base_url, "https://legacy.example/v1")

    def test_unconfigured_is_unavailable_with_actionable_error(self):
        p = provider.OpenAICompatibleImageGenProvider(Ctx())
        self.assertFalse(p.is_available())
        result = p.generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertIn("plugins.entries.openai-compatible.settings.base_url", result["error"])

    def test_remote_http_rejected_localhost_allowed(self):
        result = self.make(base_url="http://gateway.example/v1").generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertTrue(self.make(base_url="http://127.0.0.1:8317/v1").generate("draw")["success"])

    def test_http_allowed_only_for_private_networks(self):
        allowed = ["http://127.0.0.1:8317/v1", "http://localhost:8317/v1", "http://[::1]:8317/v1",
                   "http://10.0.0.5:8317/v1", "http://172.29.0.4:8317/v1", "http://192.168.1.10:8317/v1",
                   "http://100.64.0.1:8317/v1", "http://100.127.255.254:8317/v1",
                   "http://[fd7a:115c:a1e0::1]:8317/v1", "http://[::ffff:192.168.1.10]:8317/v1"]
        refused = ["http://8.8.8.8/v1", "http://169.254.169.254/v1", "http://[fe80::1]/v1",
                   "http://100.128.0.1/v1", "http://172.32.0.1/v1", "http://0.0.0.0/v1",
                   "http://224.0.0.1/v1", "http://[::ffff:8.8.8.8]/v1", "http://[2001:4860::8888]/v1",
                   "ftp://127.0.0.1/v1"]
        for url in allowed:
            self.assertEqual(provider._validate_base_url(url), url.rstrip("/"), url)
        for url in refused:
            with self.assertRaises(ValueError, msg=url):
                provider._validate_base_url(url)

    def test_http_hostname_requires_every_address_private(self):
        def fake_resolve(addresses):
            return lambda host, port, type=0: [(2, 1, 6, "", (a, 0)) for a in addresses]
        with patch.object(provider.socket, "getaddrinfo", fake_resolve(["172.29.0.4"])):
            self.assertEqual(provider._validate_base_url("http://cpa:8317/v1"), "http://cpa:8317/v1")
        with patch.object(provider.socket, "getaddrinfo", fake_resolve(["10.0.0.2", "93.184.216.34"])):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://mixed.example/v1")
        with patch.object(provider.socket, "getaddrinfo", side_effect=provider.socket.gaierror()):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://unresolvable.invalid/v1")
        with patch.object(provider.socket, "getaddrinfo", fake_resolve([])):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://empty.example/v1")

    def test_https_never_needs_resolution(self):
        with patch.object(provider.socket, "getaddrinfo", side_effect=AssertionError("no DNS for https")):
            self.assertEqual(provider._validate_base_url("https://8.8.8.8/v1"), "https://8.8.8.8/v1")

    def test_key_with_newline_rejected_without_echo(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_API_KEY": "secret-value\ninjected: header"}):
            result = self.make().generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertNotIn("secret-value", result["error"])

    # -- model and quality mapping ---------------------------------------------------

    def test_gpt_image_default_quality_and_size(self):
        self.make().generate("draw", "landscape", model="gpt-image-2")
        call = self.call()
        self.assertEqual((call["model"], call["size"], call["quality"], call["n"]),
                         ("gpt-image-2", "auto", "medium", 1))
        self.assertTrue(call["stream"])
        self.assertEqual(call["partial_images"], 2)

    def test_quality_setting_and_suffix(self):
        self.make(quality="high").generate("draw", "square", model="gpt-image-2.5-sunburst")
        self.assertEqual(self.call()["quality"], "high")
        FakeClient.instances.clear()
        result = self.make().generate("draw", "portrait", model="gpt-image-2.5-flare-max")
        call = self.call()
        self.assertEqual((call["model"], call["quality"], call["size"]), ("gpt-image-2.5-flare", "max", "auto"))
        self.assertEqual(result["model"], "gpt-image-2.5-flare-max")

    def test_unknown_quality_setting_falls_back_to_medium(self):
        self.make(quality="ultra").generate("draw", model="gpt-image-2")
        self.assertEqual(self.call()["quality"], "medium")

    def test_gateway_defined_model_sent_verbatim_without_size_or_quality(self):
        self.make().generate("draw", model="gemini-3.1-flash-image")
        call = self.call()
        self.assertEqual(call["model"], "gemini-3.1-flash-image")
        self.assertNotIn("size", call)
        self.assertNotIn("quality", call)
        self.assertNotIn("stream", call)
        self.assertNotIn("partial_images", call)

    def test_legacy_model_env_used_when_hermes_passes_none(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2.5-flare"}):
            self.make().generate("draw")
        self.assertEqual(self.call()["model"], "gpt-image-2.5-flare")

    def test_image_gen_model_beats_legacy_env(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2.5-flare"}):
            self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(self.call()["model"], "gpt-image-2")

    # -- results ---------------------------------------------------------------------

    def test_success_reports_requested_and_output_size(self):
        result = self.make().generate("draw", "square", model="gpt-image-2")
        self.assertEqual(result["requested_size"], "auto")
        self.assertEqual(result["requested_quality"], "medium")
        self.assertEqual(result["output_size"], "1536x1024")
        self.assertEqual(result["api_model"], "gpt-image-2")

    def test_edit_uses_multipart_sources(self):
        source = "data:image/png;base64," + base64.b64encode(PNG_1536x1024).decode()
        result = self.make().generate("edit", "portrait", image_url=source, model="gpt-image-2")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["modality"], "image")
        call = self.call(kind="edit_calls")
        self.assertEqual(call["image"].read(), PNG_1536x1024)
        self.assertTrue(call["stream"])
        self.assertEqual(call["partial_images"], 2)

    def test_stream_keepalives_previews_and_completion(self):
        FakeClient.events = [types.SimpleNamespace(type="keepalive")]
        FakeClient.events.extend(types.SimpleNamespace(type="image_generation.partial_image",
                                                       b64_json=FakeImage.b64_json) for index in range(3))
        FakeClient.events.append(types.SimpleNamespace(type="image_generation.completed",
                                                       b64_json=FakeImage.b64_json))
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["partial_images"]), 2)
        self.assertEqual(provider.save_b64_image.call_count, 3)
        client = FakeClient.instances[0]
        self.assertTrue(client.closed)
        self.assertTrue(client.images.stream.closed)
        self.assertEqual((client.timeout, client.max_retries), (240.0, 0))

    def test_completed_stream_without_previews(self):
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["partial_images"], [])

    def test_preview_only_stream_is_not_success(self):
        FakeClient.events = [types.SimpleNamespace(type="image_generation.partial_image",
                                                   b64_json=FakeImage.b64_json)]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "empty_response")
        self.assertTrue(FakeClient.instances[0].images.stream.closed)

    def test_stream_error_is_redacted_and_closed(self):
        FakeClient.events = [types.SimpleNamespace(type="error", message="invalid test-key-123456")]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "api_error")
        self.assertNotIn("test-key-123456", result["error"])
        self.assertTrue(FakeClient.instances[0].closed)
        self.assertTrue(FakeClient.instances[0].images.stream.closed)

    def test_timeout_during_stream_is_closed(self):
        FakeClient.events = [FakeAPITimeoutError()]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "timeout")
        self.assertTrue(FakeClient.instances[0].closed)
        self.assertTrue(FakeClient.instances[0].images.stream.closed)

    def test_stream_safety_rejection_in_nested_error(self):
        FakeClient.events = [types.SimpleNamespace(type="image_generation.failed", error={
            "code": "moderation_blocked", "message": "Request refused test-key-123456"})]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "content_policy_violation")
        self.assertIn("Safe fallback prompt:", result["error"])
        self.assertIn("approve the revised prompt", result["error"])
        self.assertNotIn("test-key-123456", result["error"])
        self.assertTrue(FakeClient.instances[0].images.stream.closed)
        self.assertTrue(FakeClient.instances[0].closed)

    def test_failed_stream_events_are_not_silent_empty_results(self):
        for event_type in ("image_generation.failed", "image_edit.error", "image_generation.incomplete"):
            with self.subTest(event_type=event_type):
                FakeClient.events = [types.SimpleNamespace(type=event_type, message="Render failed")]
                result = self.make().generate("draw", model="gpt-image-2")
                self.assertEqual(result["error_type"], "api_error")
                self.assertIn("Render failed", result["error"])
                self.assertTrue(FakeClient.instances[-1].images.stream.closed)

    def test_empty_stream_has_unknown_cause_and_retry_guidance(self):
        FakeClient.events = []
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "empty_response")
        self.assertIn("not confirmed as a safety rejection", result["error"])
        self.assertIn("reviewing the prompt", result["error"])
        self.assertIn("do not retry automatically", result["error"])

    def test_sdk_stream_exception_preserves_safety_code(self):
        failure = Exception("Stream rejected test-key-123456")
        failure.body = {"code": "moderation_blocked", "message": str(failure)}
        FakeClient.events = [failure]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "content_policy_violation")
        self.assertNotIn("test-key-123456", result["error"])
        self.assertTrue(FakeClient.instances[0].images.stream.closed)

    def test_completed_event_without_image_is_invalid(self):
        FakeClient.events = [types.SimpleNamespace(type="image_generation.completed")]
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "invalid_response")

    def test_preview_save_failure_is_io_error(self):
        FakeClient.events = [types.SimpleNamespace(type="image_generation.partial_image",
                                                   b64_json=FakeImage.b64_json)]
        provider.save_b64_image.side_effect = OSError("disk full")
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "io_error")
        self.assertTrue(FakeClient.instances[0].images.stream.closed)

    def test_real_sdk_parses_wire_sse_for_generation_and_editing(self):
        import httpx
        constructor = real_openai.OpenAI
        for is_edit in (False, True):
            with self.subTest(is_edit=is_edit):
                kind = "image_edit" if is_edit else "image_generation"
                events = [{"type": "keepalive"},
                          {"type": kind + ".partial_image", "b64_json": FakeImage.b64_json,
                           "partial_image_index": 0},
                          {"type": kind + ".completed", "b64_json": FakeImage.b64_json}]
                body = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                               for event in events)
                requests = []

                def handler(request):
                    requests.append(request)
                    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)

                def factory(**kwargs):
                    return constructor(http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)

                with patch.dict(sys.modules, {"openai": real_openai}), \
                        patch.object(real_openai, "OpenAI", side_effect=factory):
                    kwargs = {"image_url": "data:image/png;base64," + FakeImage.b64_json} if is_edit else {}
                    result = self.make().generate("draw", model="gpt-image-2", **kwargs)
                self.assertTrue(result["success"], result)
                self.assertEqual(len(result["partial_images"]), 1)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].url.path, "/v1/images/edits" if is_edit else "/v1/images/generations")
                if not is_edit:
                    payload = json.loads(requests[0].content)
                    self.assertEqual((payload["stream"], payload["partial_images"], payload["size"]), (True, 2, "auto"))

    def test_real_sdk_handles_http_and_sse_safety_rejections(self):
        import httpx
        constructor = real_openai.OpenAI
        for status in (200, 400, 403):
            with self.subTest(status=status):
                error = {"code": "moderation_blocked", "message": "Safety refusal test-key-123456"}
                requests = []

                def handler(request):
                    requests.append(request)
                    if status == 200:
                        event = {"type": "error", "error": error}
                        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                              text="event: error\ndata: " + json.dumps(event) + "\n\n")
                    return httpx.Response(status, json={"error": error})

                def factory(**kwargs):
                    return constructor(http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)

                with patch.dict(sys.modules, {"openai": real_openai}), \
                        patch.object(real_openai, "OpenAI", side_effect=factory):
                    result = self.make().generate("draw", model="gpt-image-2")
                self.assertEqual(result["error_type"], "content_policy_violation", result)
                self.assertIn("Safe fallback prompt:", result["error"])
                self.assertNotIn("test-key-123456", result["error"])
                self.assertFalse(result["success"])
                self.assertEqual(len(requests), 1)

    def wire_result(self, body, content_type="text/event-stream", is_edit=True, content_encoding=None):
        import httpx
        constructor = real_openai.OpenAI
        requests = []
        transports = []

        def handler(request):
            requests.append(request)
            headers = {"Content-Type": content_type}
            if content_encoding:
                headers["Content-Encoding"] = content_encoding
            return httpx.Response(200, headers=headers, content=body)

        def factory(**kwargs):
            client = httpx.Client(transport=httpx.MockTransport(handler))
            transports.append(client)
            return constructor(http_client=client, **kwargs)

        with patch.dict(sys.modules, {"openai": real_openai}), \
                patch.object(real_openai, "OpenAI", side_effect=factory):
            kwargs = {"image_url": "data:image/png;base64," + FakeImage.b64_json} if is_edit else {}
            result = self.make().generate("draw", model="gpt-image-2", **kwargs)
        self.assertEqual(len(requests), 1)
        self.assertTrue(all(client.is_closed for client in transports))
        return result, requests[0]

    def test_json_image_response_with_correct_or_misleading_content_type(self):
        body = json.dumps({"data": [{"b64_json": FakeImage.b64_json}]}, indent=2).encode()
        for content_type in ("application/json", "text/event-stream", "text/plain"):
            for is_edit in (True, False):
                with self.subTest(content_type=content_type, is_edit=is_edit):
                    result, request = self.wire_result(body, content_type, is_edit)
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["response_format"], "json")
                    self.assertEqual(result["partial_images"], [])
                    self.assertEqual(request.url.path, "/v1/images/edits" if is_edit else "/v1/images/generations")
                    self.assertIn(b"stream", request.content)
                    self.assertIn(b"partial_images", request.content)

    def test_gzip_json_response_and_utf8_bom(self):
        import gzip
        body = b"\xef\xbb\xbf  \n" + json.dumps({"data": [{"b64_json": FakeImage.b64_json}]}).encode()
        result, _ = self.wire_result(gzip.compress(body), content_encoding="gzip")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["response_format"], "json")

    def test_json_fallback_errors_are_redacted_and_classified(self):
        for code, expected in (("moderation_blocked", "content_policy_violation"), ("server_error", "api_error")):
            with self.subTest(code=code):
                body = json.dumps({"error": {"code": code, "message": "Refused test-key-123456"}}).encode()
                result, _ = self.wire_result(body)
                self.assertEqual(result["error_type"], expected)
                self.assertNotIn("test-key-123456", result["error"])

    def test_empty_json_fallback_is_not_success(self):
        for body in (b"{}", b'{"data": []}', b""):
            with self.subTest(body=body):
                result, _ = self.wire_result(body)
                self.assertEqual(result["error_type"], "empty_response")

    def test_malformed_json_fallback_is_invalid_response(self):
        for body in (b'{"data":', b"[]", b'{"data": [null]}', b'{"data": [{}]}', b'{"data": "bad"}'):
            with self.subTest(body=body):
                result, _ = self.wire_result(body)
                self.assertEqual(result["error_type"], "invalid_response")

    def test_sse_comments_multiline_data_and_event_name_fallback(self):
        body = (": keepalive\r\n\r\nevent: image_edit.completed\r\n"
                'data: {\r\ndata: "b64_json": "' + FakeImage.b64_json + '"\r\ndata: }\r\n')
        result, _ = self.wire_result(body.encode())
        self.assertTrue(result["success"], result)
        self.assertEqual(result["response_format"], "sse")

    def test_done_or_preview_only_sse_still_requires_final_image(self):
        for body in (b"data: [DONE]\n\n", b": keepalive\n\n", (
                'data: ' + json.dumps({"type": "image_edit.partial_image", "b64_json": FakeImage.b64_json})
                + '\n\ndata: [DONE]\n\n').encode()):
            with self.subTest(body=body[:40]):
                result, _ = self.wire_result(body)
                self.assertEqual(result["error_type"], "empty_response")

    def test_malformed_sse_is_invalid_response(self):
        result, _ = self.wire_result(b"data: {not-json}\n\n")
        self.assertEqual(result["error_type"], "invalid_response")

    def test_oversized_json_and_sse_payloads_are_rejected(self):
        payload = json.dumps({"data": [{"b64_json": FakeImage.b64_json}]}).encode()
        for body in (payload, b"data: " + payload + b"\n\n"):
            with self.subTest(body=body[:20]), patch.object(provider, "MAX_IMAGE_RESPONSE_BYTES", 64):
                result, _ = self.wire_result(body)
                self.assertEqual(result["error_type"], "invalid_response")
                self.assertIn("exceeds", result["error"])

    def test_raw_httpx_stream_failures_keep_timeout_or_connection_category(self):
        import httpx
        for failure, expected in ((httpx.ReadTimeout("slow"), "timeout"),
                                  (httpx.ReadError("disconnected"), "connection_error")):
            with self.subTest(expected=expected):
                FakeClient.events = [failure]
                result = self.make().generate("draw", model="gpt-image-2")
                self.assertEqual(result["error_type"], expected)
                self.assertTrue(FakeClient.instances[-1].images.stream.closed)

    def test_openai_org_and_project_env_never_sent_to_gateway(self):
        with patch.dict(os.environ, {"OPENAI_ORG_ID": "org-x", "OPENAI_PROJECT_ID": "proj-x"}):
            self.make().generate("draw", model="gpt-image-2")
        headers = FakeClient.instances[0].default_headers
        self.assertIsInstance(headers["OpenAI-Organization"], FakeOmit)
        self.assertIsInstance(headers["OpenAI-Project"], FakeOmit)

    # -- source images ---------------------------------------------------------------

    def test_non_image_local_source_refused_before_any_request(self):
        path = Path(self.tmp.name) / "notes.png"
        path.write_bytes(b"api_key = not-an-image\n")
        result = self.make().generate("edit", image_url=str(path), model="gpt-image-2")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("not a PNG, JPEG, WebP or GIF", result["error"])
        self.assertEqual(FakeClient.instances, [])

    def test_image_magic_bytes_accepted(self):
        webp = b"RIFF\x00\x00\x00\x00WEBPVP8 "
        for data in (PNG_1536x1024, b"\xff\xd8\xff\xe0jfif", b"GIF89a....", webp):
            self.assertEqual(provider._require_image(data, "x"), data)
        for data in (b"", b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVE"):
            with self.assertRaises(provider.SourceImageError):
                provider._require_image(data, "x")

    def test_non_image_data_url_refused(self):
        source = "data:image/png;base64," + base64.b64encode(b"#!/bin/sh\n").decode()
        result = self.make().generate("edit", image_url=source)
        self.assertEqual(result["error_type"], "io_error")
        self.assertEqual(FakeClient.instances, [])

    def test_source_url_refused_for_cgnat_tailscale_private_and_metadata(self):
        import tools.url_safety as url_safety
        for address in ("100.100.1.2", "100.64.0.1", "10.0.0.5", "127.0.0.1", "169.254.169.254",
                        "fd7a:115c:a1e0::1", "::ffff:100.100.1.2"):
            with patch.object(url_safety, "_getaddrinfo", fake_addrinfo(address)), \
                    patch.object(url_safety, "create_ssrf_safe_client",
                                 side_effect=AssertionError("must not connect")):
                result = self.make().generate("edit", image_url="https://box.tailnet.ts.net/a.png")
            self.assertEqual(result["error_type"], "io_error", address)
            self.assertIn("private", result["error"], address)
        self.assertEqual(FakeClient.instances, [])

    def test_source_url_mixed_public_and_cgnat_answer_refused(self):
        import tools.url_safety as url_safety
        with patch.object(url_safety, "_getaddrinfo", fake_addrinfo("93.184.216.34", "100.101.0.7")):
            result = self.make().generate("edit", image_url="https://mixed.example/a.png")
        self.assertEqual(result["error_type"], "io_error")

    def test_source_url_connect_time_block_is_io_error(self):
        import tools.url_safety as url_safety

        def handler(request):
            raise url_safety.SSRFConnectionBlocked("rebound to 100.100.1.2")
        factory, _ = mock_ssrf_client(handler)
        with patch.object(url_safety, "is_safe_url", return_value=True), \
                patch.object(url_safety, "create_ssrf_safe_client", factory):
            result = self.make().generate("edit", image_url="https://rebind.example/a.png")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("private", result["error"])

    def test_source_url_uses_guarded_client_without_redirects(self):
        import httpx
        import tools.url_safety as url_safety
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=PNG_1536x1024)
        factory, seen = mock_ssrf_client(handler)
        with patch.object(url_safety, "is_safe_url", return_value=True), \
                patch.object(url_safety, "create_ssrf_safe_client", factory):
            result = self.make().generate("edit", image_url="https://cdn.example/src/cat.png",
                                          model="gpt-image-2")
        self.assertTrue(result["success"], result)
        self.assertIs(seen["follow_redirects"], False)
        self.assertEqual(requests[0].headers["User-Agent"], provider.USER_AGENT)
        image = self.call(kind="edit_calls")["image"]
        self.assertEqual((image.name, image.read()), ("cat.png", PNG_1536x1024))

    def test_source_url_redirect_status_and_type_errors(self):
        import httpx
        import tools.url_safety as url_safety
        cases = [
            (httpx.Response(302, headers={"Location": "https://100.100.1.2/x.png"}), "redirected"),
            (httpx.Response(404), "HTTP 404"),
            (httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>"), "did not return an image"),
            (httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"not really"), "not a PNG"),
        ]
        for response, expected in cases:
            factory, _ = mock_ssrf_client(lambda request, r=response: r)
            with patch.object(url_safety, "is_safe_url", return_value=True), \
                    patch.object(url_safety, "create_ssrf_safe_client", factory):
                result = self.make().generate("edit", image_url="https://cdn.example/a.png?token=s3cr3t")
            self.assertEqual(result["error_type"], "io_error", expected)
            self.assertIn(expected, result["error"])
            self.assertNotIn("s3cr3t", result["error"])
        self.assertEqual(FakeClient.instances, [])

    def test_source_url_size_cap(self):
        import httpx
        import tools.url_safety as url_safety
        big = PNG_1536x1024 + b"\x00" * provider.MAX_SOURCE_IMAGE_BYTES
        factory, _ = mock_ssrf_client(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=big))
        with patch.object(url_safety, "is_safe_url", return_value=True), \
                patch.object(url_safety, "create_ssrf_safe_client", factory):
            result = self.make().generate("edit", image_url="https://cdn.example/a.png")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("50MB", result["error"])

    def test_source_url_must_be_https(self):
        result = self.make().generate("edit", image_url="http://cdn.example/a.png")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("HTTPS", result["error"])

    # -- errors ----------------------------------------------------------------------

    def test_missing_local_source_is_io_error_before_any_request(self):
        result = self.make().generate("edit", image_url="/nonexistent/cache/image.png")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("/nonexistent/cache/image.png", result["error"])
        self.assertIn("pruned", result["error"])
        self.assertEqual(FakeClient.instances, [])

    def test_invalid_data_url_is_io_error(self):
        result = self.make().generate("edit", image_url="data:image/png;base64,not-valid!")
        self.assertEqual(result["error_type"], "io_error")

    def test_http_error_passes_status_and_message(self):
        FakeClient.raise_exc = FakeAPIStatusError(
            400, {"error": {"message": "model gpt-image-9 is not enabled on this channel"}})
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "api_error")
        self.assertIn("HTTP 400", result["error"])
        self.assertIn("not enabled on this channel", result["error"])

    def test_auth_error_redacts_key(self):
        FakeClient.raise_exc = FakeAPIStatusError(
            401, {"error": {"message": "invalid token test-key-123456 Bearer abc.def"}})
        result = self.make().generate("draw")
        self.assertEqual(result["error_type"], "auth_error")
        self.assertNotIn("test-key-123456", result["error"])
        self.assertNotIn("abc.def", result["error"])

    def test_http_safety_codes_override_auth_classification(self):
        for status in (400, 403):
            for code in ("moderation_blocked", "content_policy_violation"):
                with self.subTest(status=status, code=code):
                    FakeClient.raise_exc = FakeAPIStatusError(status, {"error": {
                        "code": code, "message": "Safety refusal test-key-123456"}})
                    result = self.make().generate("draw")
                    self.assertEqual(result["error_type"], "content_policy_violation")
                    self.assertIn(f"HTTP {status}", result["error"])
                    self.assertIn("Safe fallback prompt:", result["error"])
                    self.assertNotIn("test-key-123456", result["error"])

    def test_explicit_safety_message_without_code(self):
        FakeClient.raise_exc = FakeAPIStatusError(400, {
            "message": "Your request was rejected by the safety system."})
        self.assertEqual(self.make().generate("draw")["error_type"], "content_policy_violation")

    def test_generic_moderation_configuration_error_is_not_a_refusal(self):
        FakeClient.raise_exc = FakeAPIStatusError(400, {"error": {
            "message": "Unsupported moderation parameter", "code": "invalid_argument"}})
        self.assertEqual(self.make().generate("draw")["error_type"], "api_error")

    def test_generic_permission_denial_is_still_auth_error(self):
        FakeClient.raise_exc = FakeAPIStatusError(403, {"error": {"message": "Permission denied"}})
        self.assertEqual(self.make().generate("draw")["error_type"], "auth_error")

    def test_cloudflare_1010_hint(self):
        FakeClient.raise_exc = FakeAPIStatusError(403, None, "error code: 1010")
        result = self.make().generate("draw")
        self.assertIn("Cloudflare", result["error"])

    def test_connection_and_timeout_errors(self):
        FakeClient.raise_exc = FakeAPITimeoutError()
        self.assertEqual(self.make().generate("draw")["error_type"], "timeout")
        FakeClient.raise_exc = FakeAPIConnectionError()
        self.assertEqual(self.make().generate("draw")["error_type"], "connection_error")

    def test_empty_prompt(self):
        self.assertEqual(self.make().generate("  ")["error_type"], "invalid_argument")

    # -- helpers and registration ----------------------------------------------------

    def test_image_dims_png_and_jpeg(self):
        self.assertEqual(provider._image_dims(PNG_1536x1024), "1536x1024")
        jpeg = b"\xff\xd8" + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 768, 1254) + b"\x00" * 8
        self.assertEqual(provider._image_dims(jpeg), "1254x768")
        self.assertIsNone(provider._image_dims(b"not an image"))

    def test_registers_provider_with_ctx(self):
        ctx = Ctx({"base_url": "https://gateway.example/v1"})
        provider.register(ctx)
        self.assertEqual(len(ctx.registered), 1)
        item = ctx.registered[0]
        self.assertEqual(item.name, "openai-compatible")
        self.assertEqual(item.capabilities()["max_reference_images"], 16)
        self.assertTrue(item.is_available())

    def test_user_agent_tracks_manifest_version(self):
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        version = next(line.split(":", 1)[1].strip() for line in manifest.splitlines()
                       if line.startswith("version:"))
        self.assertEqual(version, provider.PLUGIN_VERSION)
        self.assertTrue(provider.USER_AGENT.endswith("/" + version))


if __name__ == "__main__":
    unittest.main()

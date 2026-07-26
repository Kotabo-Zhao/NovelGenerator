"""测试 ResilientLLMClient — 韧性网关"""
import unittest
import time
from unittest.mock import MagicMock, patch, PropertyMock
from openai import RateLimitError, APITimeoutError, APIConnectionError, APIError

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.core.resilient_client import (
    ResilientLLMClient, RetryConfig, LLMCallMetrics,
    get_metrics, reset_metrics,
)


class MockChoice:
    def __init__(self, content=""):
        self.message = MagicMock()
        self.message.content = content
        self.delta = MagicMock()
        self.delta.content = content


class MockResponse:
    def __init__(self, content="测试输出", usage_total=100):
        self.choices = [MockChoice(content)]
        self.usage = MagicMock()
        self.usage.total_tokens = usage_total
    
    def __iter__(self):
        """支持流式迭代"""
        for char in self.choices[0].delta.content:
            chunk = MagicMock()
            chunk.choices = [MockChoice(char)]
            yield chunk


class MockStreamResponse:
    def __init__(self, text="流式输出测试内容"):
        self.text = text
    
    def __iter__(self):
        for char in self.text:
            chunk = MagicMock()
            choice = MagicMock()
            choice.delta = MagicMock()
            choice.delta.content = char
            chunk.choices = [choice]
            yield chunk


class TestRetryConfig(unittest.TestCase):
    def test_default_config(self):
        c = RetryConfig()
        self.assertEqual(c.max_retries, 3)
        self.assertEqual(c.base_delay, 1.0)
        self.assertEqual(c.max_delay, 30.0)
        self.assertEqual(c.backoff_multiplier, 2.0)
    
    def test_custom_config(self):
        c = RetryConfig(max_retries=5, base_delay=0.5, max_delay=10.0)
        self.assertEqual(c.max_retries, 5)
        self.assertEqual(c.base_delay, 0.5)
        self.assertEqual(c.max_delay, 10.0)


class TestLLMCallMetrics(unittest.TestCase):
    def test_empty_metrics(self):
        m = LLMCallMetrics()
        self.assertEqual(m.total_calls, 0)
        self.assertEqual(m.success_rate, 0.0)
        self.assertEqual(m.avg_latency_ms, 0.0)
    
    def test_success_rate(self):
        m = LLMCallMetrics(total_calls=10, successful_calls=8, failed_calls=2)
        self.assertAlmostEqual(m.success_rate, 80.0)
    
    def test_avg_latency(self):
        m = LLMCallMetrics(successful_calls=4, total_latency_ms=400.0)
        self.assertAlmostEqual(m.avg_latency_ms, 100.0)
    
    def test_summary_string(self):
        m = LLMCallMetrics(
            total_calls=10, successful_calls=8, failed_calls=2,
            retry_count=3, rate_limit_hits=1, total_latency_ms=800.0
        )
        s = m.summary()
        self.assertIn("10次", s)
        self.assertIn("80.0%", s)
        self.assertIn("重试3次", s)
        self.assertIn("限流1次", s)


class TestResilientClientCreate(unittest.TestCase):
    """非流式调用测试"""
    
    def setUp(self):
        reset_metrics()
        self.mock_client = MagicMock()
        self.resilient = ResilientLLMClient(
            self.mock_client, "deepseek-v4-flash",
            RetryConfig(max_retries=3, base_delay=0.01)  # 快速测试
        )
    
    def test_successful_call(self):
        self.mock_client.chat.completions.create.return_value = MockResponse("ok", 50)
        
        result = self.resilient.create(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.8, max_tokens=100
        )
        
        self.assertEqual(result.choices[0].message.content, "ok")
        self.mock_client.chat.completions.create.assert_called_once()
        
        m = get_metrics()
        self.assertEqual(m.total_calls, 1)
        self.assertEqual(m.successful_calls, 1)
        self.assertEqual(m.total_tokens_used, 50)
    
    def test_v4_auto_disable_thinking(self):
        """v4 模型自动注入 thinking disabled"""
        self.mock_client.chat.completions.create.return_value = MockResponse()
        
        self.resilient.create(messages=[{"role": "user", "content": "test"}])
        
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        self.assertIn("extra_body", call_kwargs)
        self.assertEqual(call_kwargs["extra_body"]["thinking"]["type"], "disabled")
    
    def test_retry_on_timeout(self):
        """超时后自动重试"""
        self.mock_client.chat.completions.create.side_effect = [
            APITimeoutError(request=MagicMock()),
            MockResponse("recovered"),
        ]
        
        result = self.resilient.create(
            messages=[{"role": "user", "content": "test"}]
        )
        
        self.assertEqual(result.choices[0].message.content, "recovered")
        self.assertEqual(self.mock_client.chat.completions.create.call_count, 2)
    
    def test_retry_on_connection_error(self):
        """连接错误后重试"""
        self.mock_client.chat.completions.create.side_effect = [
            APIConnectionError(request=MagicMock()),
            MockResponse("ok"),
        ]
        
        result = self.resilient.create(
            messages=[{"role": "user", "content": "test"}]
        )
        
        self.assertEqual(result.choices[0].message.content, "ok")
    
    def test_retry_on_rate_limit(self):
        """429 限流后等待重试"""
        mock_resp = MagicMock()
        mock_resp.headers = {"retry-after": "0.01"}
        
        self.mock_client.chat.completions.create.side_effect = [
            RateLimitError("rate limited", response=mock_resp, body=None),
            MockResponse("after_limit"),
        ]
        
        result = self.resilient.create(
            messages=[{"role": "user", "content": "test"}]
        )
        
        self.assertEqual(result.choices[0].message.content, "after_limit")
        m = get_metrics()
        self.assertEqual(m.rate_limit_hits, 1)
    
    def test_max_retries_exhausted(self):
        """重试耗尽后抛出异常"""
        self.mock_client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )
        
        with self.assertRaises(APITimeoutError):
            self.resilient.create(
                messages=[{"role": "user", "content": "test"}]
            )
        
        # 1次初始 + 3次重试 = 4次调用
        self.assertEqual(self.mock_client.chat.completions.create.call_count, 4)
        m = get_metrics()
        self.assertEqual(m.failed_calls, 1)
    
    def test_non_api_error_no_retry(self):
        """非 API 错误（如 ValueError）不重试"""
        self.mock_client.chat.completions.create.side_effect = ValueError("bad param")
        
        with self.assertRaises(ValueError):
            self.resilient.create(
                messages=[{"role": "user", "content": "test"}]
            )
        
        self.assertEqual(self.mock_client.chat.completions.create.call_count, 1)
    
    def test_exponential_backoff(self):
        """验证退避时间递增"""
        config = RetryConfig(max_retries=3, base_delay=1.0, backoff_multiplier=2.0)
        client = ResilientLLMClient(self.mock_client, "test", config)
        
        delays = [client._calc_backoff(i) for i in range(4)]
        self.assertAlmostEqual(delays[0], 1.0)
        self.assertAlmostEqual(delays[1], 2.0)
        self.assertAlmostEqual(delays[2], 4.0)
        self.assertAlmostEqual(delays[3], 8.0)
    
    def test_backoff_capped(self):
        """退避时间不超过 max_delay"""
        config = RetryConfig(max_retries=5, base_delay=1.0, 
                            backoff_multiplier=2.0, max_delay=10.0)
        client = ResilientLLMClient(self.mock_client, "test", config)
        
        self.assertEqual(client._calc_backoff(10), 10.0)  # 2^10=1024 → capped at 10


class TestResilientClientStream(unittest.TestCase):
    """流式调用测试"""
    
    def setUp(self):
        reset_metrics()
        self.mock_client = MagicMock()
        self.resilient = ResilientLLMClient(
            self.mock_client, "deepseek-v4-flash",
            RetryConfig(max_retries=2, base_delay=0.01)
        )
    
    def test_stream_success(self):
        """流式正常输出"""
        import asyncio
        
        self.mock_client.chat.completions.create.return_value = MockStreamResponse("abc")
        
        async def _collect():
            chunks = []
            async for text in self.resilient.create_stream(
                messages=[{"role": "user", "content": "test"}]
            ):
                chunks.append(text)
            return ''.join(chunks)
        
        result = asyncio.run(_collect())
        self.assertEqual(result, "abc")
        
        m = get_metrics()
        self.assertEqual(m.total_calls, 1)
        self.assertEqual(m.successful_calls, 1)
    
    def test_stream_disconnect_reconnect(self):
        """流式断线后重连继续"""
        import asyncio
        
        # 第一次：输出"ab"后断线
        first_response = MockStreamResponse("ab")
        
        # 模拟断线：在迭代到一半时抛出异常
        call_count = [0]
        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 返回一个会在迭代中途失败的响应
                class FailingStream:
                    def __iter__(self):
                        yield self._make_chunk("a")
                        yield self._make_chunk("b")
                        raise APIConnectionError(request=MagicMock())
                    def _make_chunk(self, text):
                        chunk = MagicMock()
                        choice = MagicMock()
                        choice.delta = MagicMock()
                        choice.delta.content = text
                        chunk.choices = [choice]
                        return chunk
                return FailingStream()
            else:
                # 重连后继续输出 "cd"
                return MockStreamResponse("cd")
        
        self.mock_client.chat.completions.create.side_effect = mock_create
        
        async def _collect():
            chunks = []
            async for text in self.resilient.create_stream(
                messages=[{"role": "user", "content": "test"}]
            ):
                chunks.append(text)
            return ''.join(chunks)
        
        result = asyncio.run(_collect())
        # 断线重连后应该包含断点前的内容 + 新内容
        self.assertIn("a", result)
        self.assertIn("b", result)
        self.assertEqual(call_count[0], 2)  # 确认重连了
    
    def test_stream_all_retries_fail(self):
        """流式所有重连都失败"""
        import asyncio
        
        self.mock_client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )
        
        async def _collect():
            chunks = []
            async for text in self.resilient.create_stream(
                messages=[{"role": "user", "content": "test"}]
            ):
                chunks.append(text)
            return ''.join(chunks)
        
        with self.assertRaises(APITimeoutError):
            asyncio.run(_collect())
        
        m = get_metrics()
        self.assertEqual(m.failed_calls, 1)


class TestGlobalMetrics(unittest.TestCase):
    def test_get_and_reset(self):
        reset_metrics()
        m = get_metrics()
        self.assertEqual(m.total_calls, 0)
        
        m.total_calls = 5
        self.assertEqual(get_metrics().total_calls, 5)
        
        reset_metrics()
        self.assertEqual(get_metrics().total_calls, 0)


class TestResumeMessages(unittest.TestCase):
    def test_build_resume_with_collected_text(self):
        client = ResilientLLMClient(MagicMock(), "test")
        original = [{"role": "user", "content": "写第一章"}]
        
        resumed = client._build_resume_messages(original, "已写的内容...")
        
        self.assertEqual(len(resumed), 3)
        self.assertEqual(resumed[0]["role"], "user")
        self.assertEqual(resumed[1]["role"], "assistant")
        self.assertEqual(resumed[1]["content"], "已写的内容...")
        self.assertEqual(resumed[2]["role"], "user")
        self.assertIn("继续", resumed[2]["content"])
    
    def test_build_resume_empty_collected(self):
        client = ResilientLLMClient(MagicMock(), "test")
        original = [{"role": "user", "content": "写第一章"}]
        
        resumed = client._build_resume_messages(original, "")
        
        self.assertEqual(len(resumed), 1)  # 没有已收集内容，不追加


if __name__ == '__main__':
    unittest.main()

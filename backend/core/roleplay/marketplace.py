"""Bounded, cached adapters for the public sources used by SillyTavern.

Only known source identifiers are accepted. Card contents remain inert data.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from urllib.parse import quote, urlparse

import httpx

from .card_codec import CardError, parse_card_bytes, shop_card_payload

OFFICIAL_INDEX = 'https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/main/index.json'
OFFICIAL_PREFIX = 'https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/main/assets/character/'
MAX_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024


class MarketError(ValueError):
    pass


class Marketplace:
    def __init__(self):
        self._cache = {}
        self._inflight = {}
        self._cache_bytes = 0

    async def _fetch(self, url, *, params=None):
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
            try:
                async with client.stream('GET', url, params=params,
                                         headers={'User-Agent': 'NovelGenerator/4.1', 'Accept': 'application/json,image/png,image/webp'}) as response:
                    if response.status_code != 200:
                        raise MarketError(f'角色源暂时无法访问（{response.status_code}），请稍后重试或导入本地角色卡')
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BYTES:
                            raise MarketError('角色卡超过 8MB 限制')
                    return bytes(chunks)
            except httpx.HTTPError as exc:
                raise MarketError('连接角色源超时或网络不可用，请稍后重试') from exc

    @staticmethod
    def _value_size(value):
        if isinstance(value, bytes):
            return len(value)
        if isinstance(value, str):
            return len(value.encode('utf-8'))
        if isinstance(value, dict):
            return sum(Marketplace._value_size(k) + Marketplace._value_size(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return sum(Marketplace._value_size(v) for v in value)
        return 32

    def _evict(self, key):
        self._cache_bytes -= self._cache.pop(key)[2]

    async def _cached(self, key, loader, ttl=600):
        now = time.monotonic()
        for stale in [k for k, entry in self._cache.items() if entry[0] <= now]:
            self._evict(stale)
        entry = self._cache.get(key)
        if entry:
            # Refresh insertion order for LRU eviction without extending TTL.
            self._cache[key] = self._cache.pop(key)
            return copy.deepcopy(entry[1])

        async def load():
            value = await loader()
            size = self._value_size(value)
            if size <= MAX_CACHE_BYTES:
                while self._cache and (len(self._cache) >= 64 or self._cache_bytes + size > MAX_CACHE_BYTES):
                    self._evict(next(iter(self._cache)))
                self._cache[key] = (time.monotonic() + ttl, value, size)
                self._cache_bytes += size
            return value

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(load())
            self._inflight[key] = task
            def completed(done):
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)
                # Retrieve errors even when all requesting clients disconnect.
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(completed)
        # Cancelling one browser request must not cancel another client's load.
        return copy.deepcopy(await asyncio.shield(task))

    async def official(self):
        async def load():
            try:
                items = json.loads(await self._fetch(OFFICIAL_INDEX))
                return [item for item in items if isinstance(item, dict) and item.get('type') == 'character'
                        and str(item.get('url', '')).startswith(OFFICIAL_PREFIX)]
            except (TypeError, json.JSONDecodeError) as exc:
                raise MarketError('官方资源目录格式发生变化，请稍后重试') from exc
        return await self._cached('official', load)

    async def catalog(self, source='official', query='', page=1):
        query = query.strip()[:120]
        page = max(1, min(500, page))
        if source == 'official':
            items = await self.official()
            items = [dict(reference=item['id'], name=item.get('name') or item['id'],
                          description=item.get('description', ''), creator='SillyTavern',
                          image=item['url'], tags=['官方资源'], source='official',
                          url='https://github.com/SillyTavern/SillyTavern-Content') for item in items]
            if query:
                items = [item for item in items if query.lower() in (item['name'] + item['description']).lower()]
            return {'items': items[(page-1)*24:page*24], 'total': len(items), 'page': page, 'source': source}
        if source != 'chub':
            raise MarketError('不支持的角色源')
        async def load():
            try:
                data = json.loads(await self._fetch('https://api.chub.ai/search', params={
                    'search': query, 'first': 24, 'page': page, 'nsfw': 'false', 'sort': 'download_count'}))['data']
                items = []
                for node in data.get('nodes', []):
                    try:
                        reference = self.reference('chub', str(node.get('fullPath', '')))
                    except MarketError:
                        continue
                    if node.get('nsfw_image'):
                        continue
                    image = str(node.get('avatar_url') or '')
                    items.append(dict(reference=reference, source='chub', name=str(node.get('name') or reference)[:100],
                                      description=str(node.get('tagline') or node.get('description') or '')[:700],
                                      creator=reference.split('/')[0], tags=[tag[:80] for tag in (node.get('topics') or []) if isinstance(tag, str)][:8],
                                      image=image if urlparse(image).hostname == 'avatars.charhub.io' else '',
                                      url='https://chub.ai/characters/' + reference))
                return {'items': items, 'total': int(data.get('count', 0)), 'page': page, 'source': source}
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise MarketError('社区角色目录格式发生变化，请尝试链接导入') from exc
        return await self._cached(('chub', query, page), load, ttl=180)

    @staticmethod
    def reference(source, reference):
        reference = str(reference).strip()
        if source == 'chub':
            if reference.startswith(('https://', 'http://')):
                url = urlparse(reference)
                if url.scheme != 'https' or url.hostname not in {'chub.ai', 'www.chub.ai', 'characterhub.org'}:
                    raise MarketError('请使用 Chub 的角色页链接或 作者/角色 标识')
                reference = url.path.strip('/').removeprefix('characters/')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}/[A-Za-z0-9_.-]{1,180}', reference) or '..' in reference:
                raise MarketError('角色标识格式应为 作者/角色')
        elif source == 'official':
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', reference) or '..' in reference:
                raise MarketError('官方角色标识无效')
        else:
            raise MarketError('不支持的角色源')
        return reference

    async def card(self, source, reference):
        reference = self.reference(source, reference)
        async def load():
            if source == 'official':
                item = next((item for item in await self.official() if item['id'] == reference), None)
                if not item:
                    raise MarketError('官方目录中没有找到这张角色卡')
                raw = await self._fetch(item['url'])
                card, fmt = parse_card_bytes(raw, reference)
                return card, raw if fmt == 'png' else None
            raw = await self._fetch('https://api.chub.ai/api/characters/' + quote(reference, safe='/') + '?full=true')
            try:
                node = json.loads(raw)['node']
                payload = shop_card_payload(node)
                payload['data'].setdefault('creator', reference.split('/')[0])
                card, _ = parse_card_bytes(json.dumps(payload).encode(), 'card.json')
                if not card.get('creator'):
                    card['creator'] = reference.split('/')[0]
            except (CardError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise MarketError('该角色的资料格式不兼容') from exc
            avatar = None
            image = str(node.get('avatar_url') or '')
            if urlparse(image).scheme == 'https' and urlparse(image).hostname == 'avatars.charhub.io':
                try:
                    avatar = await self._fetch(image)
                    if not avatar.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'RIFF')):
                        avatar = None
                except MarketError:
                    pass  # Avatar failure must not discard a usable card.
            return card, avatar
        card, avatar = await self._cached(('card', source, reference), load)
        card['market_source'] = source
        card['market_reference'] = reference
        return card, avatar

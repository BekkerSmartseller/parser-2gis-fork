from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
from typing import TYPE_CHECKING, Optional

from ...chrome import ChromeRemote
from ...common import wait_until_finished
from ...logger import logger
from ..utils import blocked_requests

if TYPE_CHECKING:
    from ...chrome import ChromeOptions
    from ...chrome.dom import DOMNode
    from ...writer import FileWriter
    from ..options import ParserOptions


class MainParser:
    """Main parser that extracts useful payload
    from search result pages using Chrome browser
    and saves it into a `csv`, `xlsx` or `json` files.

    Args:
        url: 2GIS URLs with items to be collected.
        chrome_options: Chrome options.
        parser_options: Parser options.
    """
    def __init__(self, url: str,
                 chrome_options: ChromeOptions,
                 parser_options: ParserOptions) -> None:
        self._options = parser_options
        self._url = url

        # "Catalog Item Document" response pattern.
        self._item_response_pattern = r'https://catalog\.api\.2gis.[^/]+/.*/items/byid'

        # Open browser, start remote
        response_patterns = [self._item_response_pattern]
        self._chrome_remote = ChromeRemote(chrome_options=chrome_options,
                                           response_patterns=response_patterns)
        self._chrome_remote.start()

        # Add counter for 2GIS requsts
        self._add_xhr_counter()

        # Disable specific requests
        blocked_urls = blocked_requests(extended=chrome_options.disable_images)
        self._chrome_remote.add_blocked_requests(blocked_urls)

    @staticmethod
    def url_pattern():
        """URL pattern for the parser.

        Принимает и городской, и координатный (без города) поиск, а также
        страницы филиалов сети:
          https://2gis.ru/sharya/search/...            (город)
          https://2gis.ru/search/фитнес?m=lon,lat/zoom  (пространственный)
          https://2gis.ru/krasnoyarsk/branches/...      (филиалы сети)
        """
        return r'https?://2gis\.[^/]+(?:/[^/]+)?/(?:search/.*|branches/.*)'

    @wait_until_finished(timeout=5, throw_exception=False)
    def _get_links(self) -> list[DOMNode]:
        """Extracts specific DOM node links from current DOM snapshot."""
        def valid_link(node: DOMNode) -> bool:
            if node.local_name == 'a' and 'href' in node.attributes:
                link_match = re.match(r'.*/(firm|station)/.*\?stat=(?P<data>[a-zA-Z0-9%]+)', node.attributes['href'])
                if link_match:
                    try:
                        base64.b64decode(urllib.parse.unquote(link_match.group('data')))
                        return True
                    except:
                        pass

            return False

        dom_tree = self._chrome_remote.get_document()
        return dom_tree.search(valid_link)

    def _add_xhr_counter(self) -> None:
        """Inject old-school wrapper around XMLHttpRequest,
        to keep track of all pending requests to 2GIS website."""
        xhr_script = r'''
            (function() {
                var oldOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url, async, user, pass) {
                    if (url.match(/^https?\:\/\/[^\/]*2gis\.[a-z]+/i)) {
                        if (window.openHTTPs == undefined) {
                            window.openHTTPs = 1;
                        } else {
                            window.openHTTPs++;
                        }
                        this.addEventListener("readystatechange", function() {
                            if (this.readyState == 4) {
                                window.openHTTPs--;
                            }
                        }, false);
                    }
                    oldOpen.call(this, method, url, async, user, pass);
                }
            })();
        '''
        self._chrome_remote.add_start_script(xhr_script)

    def _wait_requests_finished(self, timeout: int = 120) -> None:
        """Wait for all pending 2GIS requests (max `timeout` seconds).

        «Зависший» XHR к 2GIS держит счётчик window.openHTTPs > 0 вечно —
        вместо того чтобы ронять задачу (TimeoutError), ждём до таймаута,
        логируем и сбрасываем счётчик, чтобы парсинг продолжился.
        """
        deadline = time.monotonic() + max(0, int(timeout))
        while True:
            try:
                done = self._chrome_remote.execute_script('window.openHTTPs == 0')
            except Exception:  # noqa: BLE001
                return
            if done:
                return
            if time.monotonic() > deadline:
                logger.warning('[parser] ожидание ответов 2GIS превысило %ss — '
                               'сбрасываем счётчик и продолжаем.', timeout)
                try:
                    self._chrome_remote.execute_script('window.openHTTPs = 0')
                except Exception:  # noqa: BLE001
                    pass
                return
            time.sleep(0.1)

    def _get_available_pages(self) -> dict[int, DOMNode]:
        """Get available pages to navigate."""
        dom_tree = self._chrome_remote.get_document()
        dom_links = dom_tree.search(lambda x: x.local_name == 'a' and 'href' in x.attributes)

        available_pages = {}
        for link in dom_links:
            link_match = re.match(r'.*/search/.*/page/(?P<page_number>\d+)', link.attributes['href'])
            if link_match:
                available_pages[int(link_match.group('page_number'))] = link

        return available_pages

    def _go_page(self, n_page: int) -> Optional[int]:
        """Go page with number `n_page`.

        Note:
            `n_page` gotta exists in current DOM.
            Otherwise 2GIS anti-bot will redirect you to the first page.

        Args:
            n_page: Page number.

        Returns:
            Navigated page number.
        """
        available_pages = self._get_available_pages()
        if n_page in available_pages:
            self._chrome_remote.perform_click(available_pages[n_page])
            return n_page

        return None

    # --- Сбор филиалов сети (страницы /branches/...) ---

    @staticmethod
    def _is_branch_card(node: DOMNode) -> bool:
        """Карточка филиала: `<a href="/firm/{id}">` без `?stat=` (выдача ссылок
        поиска использует `?stat=`, карточки на странице /branches/ — нет)."""
        if node.local_name != 'a' or 'href' not in node.attributes:
            return False
        href = node.attributes['href']
        if '?stat=' in href:
            return False
        return re.search(r'/firm/(\d+)', href) is not None

    def _get_branch_links(self) -> list[DOMNode]:
        """Ссылки карточек филиалов на текущей странице (/branches/)."""
        dom_tree = self._chrome_remote.get_document()
        return dom_tree.search(self._is_branch_card)

    def _get_branch_urls(self) -> set[str]:
        """Полные URL `/branches/{network_id}` («N филиала») из текущего DOM.

        Ссылка вида `https://2gis.ru/krasnoyarsk/branches/70000001029980685`
        появляется рядом с карточкой сети в выдаче — по ней собираются филиалы."""
        base = re.match(r'(https?://[^/]+)', self._url)
        base = base.group(1) if base else 'https://2gis.ru'
        dom_tree = self._chrome_remote.get_document()
        urls = set()
        for node in dom_tree.search(lambda x: x.local_name == 'a' and 'href' in x.attributes):
            href = node.attributes['href']
            if '?stat=' in href or 'filters' in href:
                continue
            if re.search(r'/branches/\d+', href):
                urls.add(base + href if href.startswith('/') else href)
        return urls

    def _flush_byid_queue(self) -> None:
        """Выбрасывает накопившиеся byid-ответы (сброс перед кликом)."""
        while self._chrome_remote.poll_response(self._item_response_pattern, timeout=0.05) is not None:
            pass

    @staticmethod
    def _doc_firm_ids(doc: dict) -> list[str]:
        """Числовые id организаций (префиксы до `_`) из byid-документа."""
        ids = []
        for item in ((doc or {}).get('result') or {}).get('items') or []:
            iid = str(item.get('id') or '')
            if iid:
                ids.append(iid.split('_')[0])
        return ids

    def _drain_byid_docs(self, seconds: float, id_prefix: Optional[str] = None) -> list[dict]:
        """Собирает byid-ДОКУМЕНТЫ (полные пакеты `{meta, result}`) из очереди за `seconds`.

        Клик по карточке может вызвать 1–2 ответа (филиал + «похожая»
        организация) — фильтруем по id-префиксу карточки (`/firm/{id}`),
        чтобы в результат не попали чужие организации. Пакет принимается,
        если хотя бы один его item начинается с id-префикса.
        """
        docs: list[dict] = []
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            resp = self._chrome_remote.poll_response(self._item_response_pattern, timeout=0.3)
            if resp and resp.get('status', -1) >= 0:
                body = self._chrome_remote.get_response_body(resp, timeout=10)
                if body:
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        data = None
                    if isinstance(data, dict):
                        items = ((data.get('result') or {}).get('items')) or []
                        if id_prefix and not any(
                                str(it.get('id', '')).startswith(id_prefix) for it in items):
                            continue
                        docs.append(data)
            time.sleep(0.1)
        return docs

    def _current_branches_url(self) -> str:
        """URL страницы списка филиалов текущей сети.

        Корректно для страницы списка (`/branches/{net}`) и страницы фирмы
        внутри сети (`/branches/{net}/firm/{firm}/...`)."""
        loc = self._chrome_remote.execute_script('location.href') or self._url
        base = str(loc).split('?')[0]
        m = re.search(r'(https?://[^/]+/[^/]+/branches/\d+)', base)
        if m:
            return m.group(1)
        return self._url

    def _page_firm_ids(self) -> list[str]:
        """Числовые id филиалов из карточек текущей страницы (без дублей)."""
        ids = []
        for card in self._get_branch_links():
            m = re.search(r'/firm/(\d+)', card.attributes['href'])
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        return ids

    def _find_firm_card(self, firm_id: str) -> Optional[DOMNode]:
        """Свежий DOM-узел карточки филиала `/firm/{firm_id}` (без `?stat=`)."""
        dom_tree = self._chrome_remote.get_document()
        for node in dom_tree.search(self._is_branch_card):
            if re.search(r'/firm/%s' % re.escape(firm_id), node.attributes['href']):
                return node
        return None

    def _click_firm_and_drain(self, node: DOMNode, firm_id: str) -> list[dict]:
        """Кликает карточку филиала и собирает byid-документы этой фирмы."""
        for _ in range(3):  # 3 попытки получить ответ
            self._flush_byid_queue()
            self._chrome_remote.perform_click(node)
            if self._options.delay_between_clicks:
                self._chrome_remote.wait(self._options.delay_between_clicks / 1000)
            docs = self._drain_byid_docs(3, id_prefix=firm_id)
            if docs:
                return docs
        return []

    def _collect_branch_docs(self, writer: FileWriter, visited_links: set[str],
                             collected_ids: set[str], collected_records: int,
                             max_records: int,
                             only_firm_id: Optional[str] = None) -> int:
        """Собирает документы филиалов сети.

        Стратегия: открываем страницу списка филиалов, для каждого id кликаем
        его карточку (клик провоцирует byid-запрос филиала), после каждого клика
        возвращаемся на страницу списка (клик перерисовывает SPA-страницу, иначе
        DOM-узлы карточек «протухают»).

        Args:
            writer: куда писать документы.
            visited_links: уже посещённые href (дедуп).
            collected_ids: числовые id уже собранных организаций — филиалы,
                полученные из выдачи, повторно не собираем.
            collected_records: счётчик собранных записей.
            max_records: лимит записей.
            only_firm_id: если задан — собирать только эту фирму
                (URL `/branches/{net}/firm/{firm}/...`).

        Returns:
            Новый счётчик собранных записей.
        """
        branches_url = self._current_branches_url()

        # id филиалов: со страницы списка; при входной ссылке на фирму —
        # только эта фирма.
        firm_ids = self._page_firm_ids()
        if only_firm_id:
            firm_ids = [f for f in firm_ids if f == only_firm_id] or [only_firm_id]

        if not firm_ids:
            logger.warning('[branches] филиалы на странице не найдены (%s)', branches_url)
            # Best-effort: у сети может быть один филиал (bc=1) — пробуем вернуть
            # саму организацию через её фирм-страницу. Если id невалиден/удалён,
            # 2GIS редиректит на «город по умолчанию» и фирм-страница не вернёт
            # документ с этим id — даём явную диагностику.
            m = re.search(r'/branches/(\d+)', branches_url)
            if m and m.group(1) not in collected_ids:
                org_id = m.group(1)
                collected_ids.add(org_id)
                firm_url = branches_url.replace(f'/branches/{org_id}', f'/firm/{org_id}')
                self._chrome_remote.clear_requests()
                self._chrome_remote.navigate(firm_url, referer='https://google.com',
                                             timeout=120)
                try:
                    self._wait_requests_finished()
                except Exception:  # noqa: BLE001
                    pass
                docs = self._drain_byid_docs(5, id_prefix=org_id)
                if not docs:
                    logger.warning('[branches] организация %s не найдена в 2GIS '
                                   '(фирм-страница не вернула документ с этим id)',
                                   org_id)
                for doc in docs:
                    writer.write(doc)
                    collected_ids.update(self._doc_firm_ids(doc))
                    collected_records += 1
                    if collected_records >= max_records:
                        return collected_records
            return collected_records

        for firm_id in firm_ids:
            if firm_id in collected_ids:
                logger.info('[branches] фирма %s уже собрана — пропуск', firm_id)
                continue
            collected_ids.add(firm_id)

            # Возврат на страницу списка филиалов (свежий DOM после клика).
            self._chrome_remote.clear_requests()
            self._chrome_remote.navigate(branches_url, referer='https://google.com',
                                         timeout=120)
            try:
                self._wait_requests_finished()
            except Exception:  # noqa: BLE001
                pass

            node = self._find_firm_card(firm_id)
            if node is None:
                logger.warning('[branches] карточка фирмы %s не найдена на %s',
                               firm_id, branches_url)
                continue
            docs = self._click_firm_and_drain(node, firm_id)
            if not docs:
                logger.warning('[branches] по карточке %s byid не получен', firm_id)
            for doc in docs:
                writer.write(doc)
                collected_ids.update(self._doc_firm_ids(doc))
                collected_records += 1
                if collected_records >= max_records:
                    return collected_records
        return collected_records

    def _parse_branches_url(self, writer: FileWriter) -> None:
        """Парсинг входного URL страницы филиалов сети (/branches/...)."""
        self._chrome_remote.navigate(self._url, referer='https://google.com', timeout=120)
        try:
            self._wait_requests_finished()
        except Exception:  # noqa: BLE001
            pass
        m = re.search(r'/branches/(\d+)/firm/(\d+)', self._url)
        only_firm_id = m.group(2) if m else None
        logger.info('[branches] входная ссылка филиалов, фирма-фильтр: %s', only_firm_id or 'все')
        self._collect_branch_docs(writer, set(), set(), 0, self._options.max_records,
                                  only_firm_id=only_firm_id)

    def parse(self, writer: FileWriter) -> None:
        """Parse URL with result items.

        Args:
            writer: Target file writer.
        """
        # Входная ссылка на страницу филиалов сети — собираем напрямую
        # (без поисковой выдачи).
        if '/branches/' in self._url:
            self._parse_branches_url(writer)
            return

        # Starting from page 6 and further
        # 2GIS redirects user to the beginning automatically (anti-bot protection).
        # If a page argument found in the URL, we should manually walk to it first.

        current_page_number = 1
        url = re.sub(r'/page/\d+', '', self._url, re.I)

        page_match = re.search(r'/page/(?P<page_number>\d+)', self._url, re.I)
        if page_match:
            walk_page_number = int(page_match.group('page_number'))
        else:
            walk_page_number = None

        # Go URL
        self._chrome_remote.navigate(url, referer='https://google.com', timeout=120)

        # Document loaded, get its response
        responses = self._chrome_remote.get_responses(timeout=5)
        if not responses:
            logger.error('Ошибка получения ответа сервера.')
            return
        document_response = responses[0]

        # Handle 404
        assert document_response['mimeType'] == 'text/html'
        if document_response['status'] == 404:
            logger.warn('Сервер вернул сообщение "Точных совпадений нет / Не найдено".')
            # Fallback: в городах, где рубрика отсутствует, маршрут /rubricId/ даёт 404,
            # хотя обычный текстовый поиск по тому же термину находит организации.
            fallback_url = re.sub(r'/rubricId/[^/]+', '', url, flags=re.I)
            if fallback_url != url:
                logger.info('404, повторный поиск без рубрики: %s', fallback_url)
                # Сбрасываем буфер ответов: navigate() его не очищает, поэтому
                # get_responses() вернул бы старый 404 и ретрай ложно провалился.
                self._chrome_remote.clear_requests()
                self._chrome_remote.navigate(
                    fallback_url, referer='https://google.com', timeout=120)
                retry_responses = self._chrome_remote.get_responses(timeout=5)
                retry_doc = next(
                    (r for r in retry_responses
                     if r.get('mimeType') == 'text/html' and r.get('status') != 404),
                    None)
                if retry_doc:
                    document_response = retry_doc
                    url = fallback_url
                    logger.info('Ретрай успешен, парсинг продолжается.')
                else:
                    logger.warn('Ретрай без рубрики тоже вернул 404.')

            if self._options.skip_404_response and document_response['status'] == 404:
                return

        # Parsed records
        collected_records = 0

        # Already visited links
        visited_links: set[str] = set()

        # Числовые id уже собранных организаций — для дедупа при сборе филиалов
        # (филиал, полученный из выдачи, со страницы /branches/ повторно не берём).
        collected_ids: set[str] = set()

        # Ссылки на страницы филиалов сетей («N филиала») из выдачи
        # — по ним после основного цикла собираются все филиалы.
        branch_urls: set[str] = set()

        # This wrapper is not necessary, but I'd like to be sure
        # we haven't gathered links from old DOM somehow.
        @wait_until_finished(timeout=10, throw_exception=False)
        def get_unique_links() -> list[DOMNode]:
            links = self._get_links()
            link_addresses = set(x.attributes['href'] for x in links)
            if link_addresses & visited_links:
                return []

            visited_links.update(link_addresses)
            return links

        while True:
            # Wait all 2GIS requests get finished
            self._wait_requests_finished()

            # Gather links to be clicked
            links = get_unique_links()

            # Собираем ссылки «N филиала» для последующего сбора филиалов сети
            if self._options.collect_branches:
                branch_urls.update(self._get_branch_urls())

            # We should parse the page if we are not walking
            if not walk_page_number:
                # Iterate through gathered links
                for link in links:
                    for _ in range(3):  # 3 attempts to get response
                        # Click the link to provoke request
                        # with a auth key and secret arguments
                        self._chrome_remote.perform_click(link)

                        # Delay between clicks, could be usefull if
                        # 2GIS's anti-bot service become more strict.
                        if self._options.delay_between_clicks:
                            self._chrome_remote.wait(self._options.delay_between_clicks / 1000)

                        # Gather response and collect useful payload.
                        resp = self._chrome_remote.wait_response(self._item_response_pattern)

                        # If request is failed - repeat, otherwise go further.
                        if resp and resp['status'] >= 0:
                            break

                    # Get response body data
                    if resp and resp['status'] >= 0:
                        data = self._chrome_remote.get_response_body(resp, timeout=10) if resp else None

                        try:
                            doc = json.loads(data)
                        except json.JSONDecodeError:
                            logger.error('Сервер вернул некорректный JSON документ: "%s", пропуск позиции.', data)
                            doc = None
                    else:
                        doc = None

                    if doc:
                        # Write API document into a file
                        writer.write(doc)
                        collected_ids.update(self._doc_firm_ids(doc))
                        collected_records += 1
                    else:
                        logger.error('Данные не получены, пропуск позиции.')

                    # We've reached our limit, bail
                    if collected_records >= self._options.max_records:
                        logger.info('Спарсено максимально разрешенное количество записей с данного URL.')
                        return

            # Evaluate Garbage Collection if it's been exposed and enabled
            if self._options.use_gc and current_page_number % self._options.gc_pages_interval == 0:
                logger.debug('Запуск сборщика мусора.')
                self._chrome_remote.execute_script('"gc" in window && window.gc()')

            # Free memory allocated for collected requests
            self._chrome_remote.clear_requests()

            # Calculate next page number and navigate it
            if walk_page_number:
                available_pages = self._get_available_pages()
                available_pages_ahead = {k: v for k, v in available_pages.items()
                                         if k > current_page_number}
                next_page_number = min(available_pages_ahead, key=lambda n: abs(n - walk_page_number),  # type: ignore
                                       default=current_page_number + 1)
            else:
                next_page_number = current_page_number + 1

            current_page_number = self._go_page(next_page_number)  # type: ignore
            if not current_page_number:
                break  # Reached the end of the search results

            # Unset walking page if we've done walking to the desired page
            if walk_page_number and walk_page_number <= current_page_number:
                walk_page_number = None

        # Сбор всех филиалов сетей, найденных в выдаче: 2GIS показывает сеть
        # одной карточкой (например при рубричном поиске) — филиалы добираем
        # со страницы /branches/{network_id}.
        if self._options.collect_branches and branch_urls \
                and collected_records < self._options.max_records:
            logger.info('[branches] найдено сетей для сбора филиалов: %d', len(branch_urls))
            for branch_url in sorted(branch_urls):
                if collected_records >= self._options.max_records:
                    break
                logger.info('[branches] сбор филиалов сети: %s', branch_url)
                self._chrome_remote.clear_requests()
                self._chrome_remote.navigate(branch_url, referer='https://google.com',
                                             timeout=120)
                try:
                    self._wait_requests_finished()
                except Exception:  # noqa: BLE001
                    pass
                collected_records = self._collect_branch_docs(
                    writer, visited_links, collected_ids, collected_records,
                    self._options.max_records)
                if collected_records >= self._options.max_records:
                    logger.info('Спарсено максимально разрешенное количество записей.')
                    return

    def close(self) -> None:
        self._chrome_remote.stop()

    def __enter__(self) -> MainParser:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __repr__(self) -> str:
        classname = self.__class__.__name__
        return (f'{classname}(parser_options={self._options!r}, '
                f'chrome_remote={self._chrome_remote!r}, '
                f'url={self._url!r})')

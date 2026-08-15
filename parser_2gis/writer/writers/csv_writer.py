from __future__ import annotations

import csv
import os
import re
import shutil
from typing import Any, Callable

from pydantic import ValidationError

from ...common import report_from_validation_error
from ...logger import logger
from ..models import CatalogItem
from ..record import _average_check, rubric_section_name, rubric_sections
from .file_writer import FileWriter


class CSVWriter(FileWriter):
    """Writer to CSV table."""
    @property
    def _type_names(self) -> dict[str, str]:
        return {
            'parking': 'Парковка',
            'street': 'Улица',
            'road': 'Дорога',
            'crossroad': 'Перекрёсток',
            'station': 'Остановка',
        }

    @property
    def _complex_mapping(self) -> dict[str, Any]:
        # Complex mapping means its content could contain several entities bound by user settings.
        # For example: phone -> phone_1, phone_2, ..., phone_n
        return {
            'phone': 'Телефон', 'email': 'E-mail', 'website': 'Веб-сайт', 'instagram': 'Instagram',
            'twitter': 'Twitter', 'facebook': 'Facebook', 'vkontakte': 'ВКонтакте', 'whatsapp': 'WhatsApp',
            'viber': 'Viber', 'telegram': 'Telegram', 'youtube': 'YouTube', 'skype': 'Skype'
        }

    @property
    def _data_mapping(self) -> dict[str, Any]:
        # Логический порядок колонок. Контактные *_1.._N и *_comment добавляются
        # ниже и вставляются рядом со своими базовыми колонками через пересортировку.
        data_mapping = {
            'name': 'Наименование',
            'description': 'Описание',
            'firm_id': 'ID в 2GIS', 'org_id': 'ID организации',
            'rubric_section': 'Раздел', 'primary_rubric': 'Основная рубрика',
            'sub_rubrics': 'Подрубрики', 'rubrics': 'Все рубрики',
            'address': 'Адрес', 'address_comment': 'Комментарий к адресу',
            'postcode': 'Почтовый индекс', 'city': 'Город', 'district': 'Район',
            'district_area': 'Округ', 'region': 'Регион', 'country': 'Страна',
            'living_area': 'Микрорайон', 'timezone': 'Часовой пояс',
            'point_lat': 'Широта', 'point_lon': 'Долгота',
            'schedule': 'Часы работы', 'schedule_comment': 'Комментарий к расписанию',
            'general_rating': 'Рейтинг', 'general_review_count': 'Количество отзывов',
            'review_count_with_stars': 'Отзывы со звёздами',
            'org_rating': 'Рейтинг организации', 'org_review_count': 'Отзывы организации',
            'phone_1': 'Телефон', 'mobile': 'Мобильный телефон', 'website_1': 'Веб-сайт',
            'email_1': 'E-mail', 'whatsapp_1': 'WhatsApp', 'telegram_1': 'Telegram',
            'instagram_1': 'Instagram', 'vkontakte_1': 'ВКонтакте',
            'average_check': 'Средний чек / цены',
            'branch_count': 'Кол-во филиалов',
            'websites': 'Веб-сайты (все)',
            'nearest_station': 'Остановка', 'station_distance': 'Расстояние до остановки, м',
            'stations': 'Остановки (все)', 'photos': 'Фото URL',
            'url': '2GIS URL', 'reviews_url': 'Ссылка на отзывы',
        }

        # Expand complex mapping (values + comment columns)
        for k, v in self._complex_mapping.items():
            for n in range(1, self._options.csv.columns_per_entity + 1):
                data_mapping[f'{k}_{n}'] = f'{v} {n}'
            data_mapping[f'{k}_comment'] = f'Комментарий: {v}'

        if not self._options.csv.add_rubrics:
            data_mapping.pop('rubrics', None)

        full_mapping = {
            **data_mapping,
            **{
                'point_lat': 'Широта',
                'point_lon': 'Долгота',
                'type': 'Тип',
            }
        }

        # Clean preset: keep only essential, human-readable columns. Empty-column
        # removal later renames single complex columns (e.g. "Телефон 1" -> "Телефон").
        if self._options.csv.clean:
            clean_keys = [
                'name', 'rubrics', 'address', 'address_comment', 'city', 'district', 'region',
                'general_rating', 'general_review_count',
                'primary_rubric', 'rubric_section', 'sub_rubrics', 'branch_count', 'average_check',
                'nearest_station', 'station_distance',
                'phone_1', 'phone_comment', 'whatsapp_1', 'instagram_1', 'telegram_1',
                'email_1', 'website_1', 'websites', 'firm_id', 'url', 'mobile', 'postcode',
            ]
            return {k: v for k, v in full_mapping.items() if k in clean_keys}

        return full_mapping

    def _writerow(self, row: dict[str, Any]) -> None:
        """Write a `row` into CSV."""
        if self._options.verbose:
            logger.info('Парсинг [%d] > %s', self._wrote_count + 1, row['name'])

        try:
            self._writer.writerow(row)
        except Exception as e:
            logger.error('Ошибка во время записи: %s', e)

    def __enter__(self) -> CSVWriter:
        super().__enter__()
        # `extrasaction='ignore'`: `_extract_raw` always fills every possible
        # field, but the clean preset narrows the column set — ignore the extras.
        self._writer = csv.DictWriter(self._file, self._data_mapping.keys(),
                                      extrasaction='ignore')
        self._writer.writerow(self._data_mapping)  # Write header
        self._wrote_count = 0
        return self

    def __exit__(self, *exc_info) -> None:
        super().__exit__(*exc_info)
        if self._options.csv.remove_empty_columns:
            logger.info('Удаление пустых колонок CSV.')
            self._remove_empty_columns()
        if self._options.csv.remove_duplicates:
            logger.info('Удаление повторяющихся записей CSV.')
            self._remove_duplicates()
        self._add_excel_separator_hint()

    def _add_excel_separator_hint(self) -> None:
        """Prepend a `sep=,` directive line.

        Excel in many locales (RU/KZ/…) defaults its list separator to ';', so a
        comma-delimited CSV lands entirely in column A. The leading `sep=,` line
        tells Excel to use a comma regardless of locale; Excel consumes the line
        and does not show it. (XLSXWriter skips this line when converting.)
        """
        tmp_csv_name = os.path.splitext(self._file_path)[0] + '.sep.csv'
        with self._open_file(tmp_csv_name, 'w') as f_out, \
                self._open_file(self._file_path, 'r') as f_in:
            f_out.write('sep=,\n')
            shutil.copyfileobj(f_in, f_out)
        shutil.move(tmp_csv_name, self._file_path)

    def _remove_empty_columns(self) -> None:
        """Post-process: Remove empty columns."""
        complex_columns = self._complex_mapping.keys()
        complex_columns_count = {c: 0 for c in self._data_mapping.keys() if
                                 re.match('|'.join(fr'^{x}_\d+$' for x in complex_columns), c)}

        # Looking for empty columns
        with self._open_file(self._file_path, 'r') as f_csv:
            csv_reader = csv.DictReader(f_csv, self._data_mapping.keys())  # type: ignore
            next(csv_reader, None)  # Skip header
            for row in csv.DictReader(f_csv, self._data_mapping.keys()):  # type: ignore
                for column_name in complex_columns_count.keys():
                    if row[column_name] != '':
                        complex_columns_count[column_name] += 1

        # Generate new data mapping
        new_data_mapping: dict[str, Any] = {}
        for k, v in self._data_mapping.items():
            if k in complex_columns_count:
                if complex_columns_count[k] > 0:
                    new_data_mapping[k] = v
            else:
                new_data_mapping[k] = v

        # Rename single complex column - remove postfix numbers
        for column in complex_columns:
            if f'{column}_1' in new_data_mapping and f'{column}_2' not in new_data_mapping:
                new_data_mapping[f'{column}_1'] = re.sub(r'\s+\d+$', '', new_data_mapping[f'{column}_1'])

        # Populate new csv
        tmp_csv_name = os.path.splitext(self._file_path)[0] + '.removed-columns.csv'

        with self._open_file(tmp_csv_name, 'w') as f_tmp_csv, \
                self._open_file(self._file_path, 'r') as f_csv:
            csv_writer = csv.DictWriter(f_tmp_csv, new_data_mapping.keys())  # type: ignore
            csv_reader = csv.DictReader(f_csv, self._data_mapping.keys())  # type: ignore
            csv_writer.writerow(new_data_mapping)  # Write new header
            next(csv_reader, None)  # Skip header

            for row in csv_reader:
                new_row = {k: v for k, v in row.items() if k in new_data_mapping}
                csv_writer.writerow(new_row)

        # Replace original table with new one
        shutil.move(tmp_csv_name, self._file_path)

    def _remove_duplicates(self) -> None:
        """Post-process: Remove duplicates."""
        tmp_csv_name = os.path.splitext(self._file_path)[0] + '.deduplicated.csv'
        with self._open_file(tmp_csv_name, 'w') as f_tmp_csv, \
                self._open_file(self._file_path, 'r') as f_csv:
            seen_records = set()
            for line in f_csv:
                if line in seen_records:
                    continue

                seen_records.add(line)
                f_tmp_csv.write(line)

        # Replace original table with new one
        shutil.move(tmp_csv_name, self._file_path)

    def write(self, catalog_doc: Any) -> None:
        """Write Catalog Item API JSON document down to CSV table.

        Args:
            catalog_doc: Catalog Item API JSON document.
        """
        if not self._check_catalog_doc(catalog_doc):
            return

        row = self._extract_raw(catalog_doc)
        if row:
            self._writerow(row)
            self._wrote_count += 1

    def _extract_raw(self, catalog_doc: Any) -> dict[str, Any]:
        """Extract data from Catalog Item API JSON document.

        Args:
            catalog_doc: Catalog Item API JSON document.

        Returns:
            Dictionary for CSV row.
        """
        data: dict[str, Any] = {k: None for k in self._data_mapping.keys()}

        item = catalog_doc['result']['items'][0]

        try:
            catalog_item = CatalogItem(**item)
        except ValidationError as e:
            errors = []
            errors_report = report_from_validation_error(e, item)
            for path, description in errors_report.items():
                arg = description['invalid_value']
                error_msg = description['error_message']
                errors.append(f'[*] Поле: {path}, значение: {arg}, ошибка: {error_msg}')

            error_str = 'Ошибка парсинга:\n' + '\n'.join(errors)
            error_str += '\nДокумент каталога: ' + str(catalog_doc)
            logger.error(error_str)

            return {}

        # Name, description
        if catalog_item.name_ex:
            data['name'] = catalog_item.name_ex.primary
            data['description'] = catalog_item.name_ex.extension
        elif catalog_item.name:
            data['name'] = catalog_item.name
        elif catalog_item.type in self._type_names:
            data['name'] = self._type_names[catalog_item.type]

        # Type
        data['type'] = catalog_item.type

        # Address
        data['address'] = catalog_item.address_name

        # Reviews
        if catalog_item.reviews:
            data['general_rating'] = catalog_item.reviews.general_rating
            data['general_review_count'] = catalog_item.reviews.general_review_count
            data['org_rating'] = catalog_item.reviews.org_rating
            data['org_review_count'] = catalog_item.reviews.org_review_count

        # Point location
        if catalog_item.point:
            data['point_lat'] = catalog_item.point.lat  # Latitude (широта)
            data['point_lon'] = catalog_item.point.lon  # Longitude (долгота)

        # Branches / photos / stations / prices
        if catalog_item.org:
            data['branch_count'] = catalog_item.org.branch_count
            data['org_id'] = catalog_item.org.id
        # Остановки сортируем по расстоянию: в «Остановки (все)» каждая выводится как
        # «Название (N м)», а в отдельную колонку «Расстояние» — минимальное значение.
        if catalog_item.links and catalog_item.links.nearest_stations:
            stations_sorted = sorted(catalog_item.links.nearest_stations,
                                     key=lambda x: x.distance if x.distance is not None else 10 ** 9)
            first = stations_sorted[0]
            data['nearest_station'] = first.name
            data['station_distance'] = first.distance
            station_names = []
            for s in stations_sorted:
                if s.name:
                    station_names.append(s.name + (f' ({s.distance} м)' if s.distance is not None else ''))
            data['stations'] = self._options.csv.join_char.join(station_names[:5])
        data['photos'] = self._options.csv.join_char.join(
            content.main_photo_url for content in catalog_item.external_content
            if content.main_photo_url)
        data['reviews_url'] = catalog_item.reviews_url
        data['firm_id'] = catalog_item.id.split('_')[0] if catalog_item.id else None
        data['mobile'] = None
        for contact_group_item in catalog_item.contact_groups:
            for contact in contact_group_item.contacts:
                if contact.type == 'phone':
                    digits = re.sub(r'\D', '', (contact.text or contact.value) or '')
                    if re.match(r'^(?:7|8)?9\d{9}$', digits):
                        data['mobile'] = contact.text or contact.value
                        break
            if data['mobile']:
                break
        data['average_check'] = _average_check(catalog_item)
        primary_rubric, sub_rubrics = rubric_sections(catalog_item)
        data['primary_rubric'] = primary_rubric
        data['sub_rubrics'] = sub_rubrics
        primary_rubric_id = None
        for rubric_item in catalog_item.rubrics:
            if rubric_item.kind == 'primary':
                primary_rubric_id = rubric_item.id
                break
        data['rubric_section'] = rubric_section_name(primary_rubric_id)
        if catalog_item.schedule and catalog_item.schedule.comment:
            data['schedule_comment'] = catalog_item.schedule.comment
        if catalog_item.reviews:
            data['review_count_with_stars'] = catalog_item.reviews.general_review_count_with_stars

        # Address comment
        data['address_comment'] = catalog_item.address_comment

        # Post code
        if catalog_item.address:
            data['postcode'] = catalog_item.address.postcode

        # Timezone
        if catalog_item.timezone is not None:
            data['timezone'] = catalog_item.timezone

        # Administrative location details
        for div in catalog_item.adm_div:
            for t in ('country', 'region', 'district_area', 'city', 'district', 'living_area'):
                if div.type == t:
                    data[t] = div.name

        # Item URL
        data['url'] = catalog_item.url

        # Contacts
        for contact_group in catalog_item.contact_groups:
            def append_contact(contact_type: str, priority_fields: list[str],
                               formatter: Callable[[str], str] | None = None) -> None:
                """Add contact to `data`.

                Args:
                    contact_type: Contact type (see `Contact` in `catalog_item.py`)
                    priority_fields: Field of contact to be added, sorted by priority
                    formatter: Field value formatter
                """
                contacts = [x for x in contact_group.contacts if x.type == contact_type]
                for i, contact in enumerate(contacts, 1):
                    contact_value = None

                    for field in priority_fields:
                        if hasattr(contact, field):
                            contact_value = getattr(contact, field)
                            break

                    # Empty contact value, bail
                    if not contact_value:
                        return

                    data_name = f'{contact_type}_{i}'
                    if data_name in data:
                        data[data_name] = formatter(contact_value) if formatter else contact_value

                        # Add comment on demand (separate column, not glued to value)
                        if self._options.csv.add_comments and contact.comment:
                            comment_name = f'{contact_type}_comment'
                            if comment_name in data:
                                data[comment_name] = contact.comment

            # URLs
            for t in ['website', 'vkontakte', 'whatsapp', 'viber', 'telegram',
                      'instagram', 'facebook', 'twitter', 'youtube', 'skype']:
                append_contact(t, ['url'])

        # Все веб-сайты (массивом через join_char) — для совместимости.
        websites: list[str] = []
        for contact_group in catalog_item.contact_groups:
            for contact in contact_group.contacts:
                if contact.type == 'website' and contact.url:
                    url = contact.url.split('?')[0]
                    if url and url not in websites:
                        websites.append(url)
        if websites:
            data['websites'] = self._options.csv.join_char.join(websites)

        # Remove arguments from WhatsApp URL
        for field in data:
            if field.startswith('whatsapp') and data[field]:
                data[field] = data[field].split('?')[0]

            # Values
            for t in ['email', 'skype']:
                append_contact(t, ['value'])
            # Comment-only contacts (no value, but have a comment)
            for contact in contact_group.contacts:
                if contact.comment and contact.type not in {'phone', 'email', 'website'}:
                    comment_name = f'{contact.type}_comment'
                    if comment_name in data and not data.get(comment_name):
                        data[comment_name] = contact.comment

            # Phone (`value` sometimes has strange crap inside, so we better parse `text`.
            # If no `text` field in contact - use `value` attribute)
            append_contact('phone', ['text', 'value'],
                           formatter=lambda x: re.sub(r'^\+7', '8', re.sub(r'[^0-9+]', '', x)))

        # Schedule
        if catalog_item.schedule:
            data['schedule'] = catalog_item.schedule.to_str(self._options.csv.join_char,
                                                            self._options.csv.add_comments)

        # Rubrics
        if self._options.csv.add_rubrics:
            data['rubrics'] = self._options.csv.join_char.join(x.name for x in catalog_item.rubrics)

        return data

"""Offline document resource limits, exercised with real PDFs and DOCX archives."""
from io import BytesIO
import base64
import warnings
import zipfile
import zlib

import pytest
from pypdf import PdfWriter, get_configuration
from pypdf.generic import ArrayObject, DecodedStreamObject, EncodedStreamObject, DictionaryObject, NameObject, NumberObject, StreamObject

from app.attachments.parsers import ParseError, parse


NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def docx_bytes(body):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', body)
    return output.getvalue()


def document(paragraphs):
    return f'<w:document xmlns:w="{NS}"><w:body>{paragraphs}</w:body></w:document>'


def paragraph(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def pdf_bytes(pages, *, array=False, to_unicode=None):
    """A real text PDF with optional large comments in each page's content."""
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    if to_unicode is not None:
        cmap = DecodedStreamObject()
        cmap.set_data(to_unicode)
        font[NameObject('/ToUnicode')] = writer._add_object(cmap.flate_encode())
    for contents in pages:
        page = writer.add_blank_page(width=200, height=200)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)}),
        })
        refs = []
        for content in contents if array else [contents]:
            if isinstance(content, StreamObject):
                stream = content
            else:
                stream = DecodedStreamObject()
                stream.set_data(content)
                stream = stream.flate_encode()
            refs.append(writer._add_object(stream))
        page[NameObject('/Contents')] = ArrayObject(refs) if array else refs[0]
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


TEXT = b'BT /F1 12 Tf 20 100 Td (hello) Tj ET\n'


def encoded_stream(data, filters):
    stream = EncodedStreamObject()
    StreamObject.set_data(stream, data)
    stream[NameObject('/Filter')] = ArrayObject([NameObject(name) for name in filters])
    return stream


@pytest.mark.parametrize('filters', [
    ['/ASCII85Decode'], ['/A85'], ['/FlateDecode', '/ASCII85Decode'],
], ids=['ascii85', 'alias', 'chain'])
def test_pdf_ascii85_output_is_bounded_before_decode(tmp_path, monkeypatch, filters):
    from pypdf.filters import ASCII85Decode
    data = b'z' * (2 * 1024 * 1024 + 1) + b'~>'
    if filters[0] == '/FlateDecode':
        data = zlib.compress(data)
    path = tmp_path / 'ascii85.pdf'
    path.write_bytes(pdf_bytes([encoded_stream(data, filters)]))
    decode = ASCII85Decode.decode
    outputs = []

    def observe(data, *args, **kwargs):
        result = decode(data, *args, **kwargs)
        outputs.append(len(result))
        return result

    monkeypatch.setattr(ASCII85Decode, 'decode', observe)
    try:
        with pytest.raises(ParseError, match='PDF.*(复杂|解压|上限)'):
            parse(path, '.pdf')
    finally:
        assert all(size <= 8 * 1024 * 1024 for size in outputs), 'ASCII85 allocated oversized output'


def test_pdf_keeps_common_ascii85_flate_chain(tmp_path):
    path = tmp_path / 'normal-ascii85.pdf'
    data = base64.a85encode(zlib.compress(TEXT), adobe=True)
    path.write_bytes(pdf_bytes([encoded_stream(data, ['/ASCII85Decode', '/FlateDecode'])]))
    assert parse(path, '.pdf')['text'] == '[第 1 页]\nhello'


def test_pdf_content_limits_do_not_change_font_resource_behavior(tmp_path):
    path = tmp_path / 'font.pdf'
    cmap = b'%' + b'a' * (9 * 1024 * 1024) + b'\n1 beginbfchar\n<68> <4F60>\nendbfchar\n'
    path.write_bytes(pdf_bytes([TEXT], to_unicode=cmap))
    assert parse(path, '.pdf')['text'] == '[第 1 页]\n你ello'


def form_pdf_bytes(form_text, *, nested=False, cycle=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    resources = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    form = DecodedStreamObject()
    form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([NumberObject(n) for n in [0, 0, 200, 200]]),
                 NameObject('/Resources'): resources})
    form.set_data(form_text)
    form = form.flate_encode()
    ref = writer._add_object(form)
    if cycle:
        resources[NameObject('/XObject')] = DictionaryObject({NameObject('/Fm0'): ref})
    if nested:
        outer = DecodedStreamObject()
        outer.update(form)
        outer[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Inner'): ref})})
        outer.set_data(b'/Inner Do\n')
        outer.pop('/Filter', None)
        ref = writer._add_object(outer)
    page[NameObject('/Resources')] = DictionaryObject(resources)
    page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Fm0'): ref})
    contents = DecodedStreamObject()
    contents.set_data(TEXT + b'/Fm0 Do\n')
    page[NameObject('/Contents')] = writer._add_object(contents)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize('nested', [False, True], ids=['form', 'nested-form'])
def test_pdf_form_limits_fail_instead_of_returning_partial_text(tmp_path, nested):
    path = tmp_path / 'form-limit.pdf'
    path.write_bytes(form_pdf_bytes(TEXT + b'%' + b'a' * (8 * 1024 * 1024) + b'\n', nested=nested))
    with pytest.raises(ParseError, match='PDF.*(复杂|解压|上限)'):
        parse(path, '.pdf')


def test_pdf_normal_nested_forms_keep_their_text(tmp_path):
    path = tmp_path / 'forms.pdf'
    path.write_bytes(form_pdf_bytes(TEXT.replace(b'hello', b'form text'), nested=True))
    text = parse(path, '.pdf')['text']
    assert 'hello' in text and 'form text' in text


def test_pdf_form_cycles_fail_clearly(tmp_path):
    path = tmp_path / 'cycle.pdf'
    path.write_bytes(form_pdf_bytes(TEXT + b'/Fm0 Do\n', cycle=True))
    with pytest.raises(ParseError, match='PDF.*(循环|复杂)'):
        parse(path, '.pdf')


def test_pdf_stops_oversized_flate_stream_before_full_decode(tmp_path, monkeypatch):
    path = tmp_path / 'compressed.pdf'
    path.write_bytes(pdf_bytes([TEXT + b'%' + b'a' * (8 * 1024 * 1024) + b'\n']))
    original = EncodedStreamObject.get_data
    decoded = []

    def observe(stream):
        data = original(stream)
        decoded.append(len(data))
        return data

    monkeypatch.setattr(EncodedStreamObject, 'get_data', observe)
    try:
        with pytest.raises(ParseError, match='PDF.*(复杂|解压|上限)'):
            parse(path, '.pdf')
    finally:
        assert all(size <= 8 * 1024 * 1024 for size in decoded), 'Oversized stream was fully decoded'


def test_pdf_rejects_merged_page_contents_over_limit(tmp_path, monkeypatch):
    path = tmp_path / 'array.pdf'
    part = b'%' + b'a' * (5 * 1024 * 1024) + b'\n'
    path.write_bytes(pdf_bytes([[TEXT + part, part]], array=True))
    original = EncodedStreamObject.get_data
    decoded_bytes = 0

    def observe(stream):
        nonlocal decoded_bytes
        cached = stream.decoded_self is not None
        data = original(stream)
        if not cached:
            decoded_bytes += len(data)
        return data

    monkeypatch.setattr(EncodedStreamObject, 'get_data', observe)
    before = get_configuration()
    try:
        with pytest.raises(ParseError, match='PDF.*(复杂|解压|上限)'):
            parse(path, '.pdf')
    finally:
        assert get_configuration() == before
        assert decoded_bytes <= 8 * 1024 * 1024, 'Merged page decoded beyond its budget'


def test_pdf_stops_at_cumulative_page_budget(tmp_path, monkeypatch):
    path = tmp_path / 'many-streams.pdf'
    # Each page is below 8 MiB, while the fifth page would exceed 32 MiB total.
    content = TEXT + b'%' + b'a' * (7 * 1024 * 1024) + b'\n'
    path.write_bytes(pdf_bytes([content] * 5))
    original = EncodedStreamObject.get_data
    decoded_bytes = 0

    def observe(stream):
        nonlocal decoded_bytes
        was_cached = stream.decoded_self is not None
        data = original(stream)
        if not was_cached:
            decoded_bytes += len(data)
        return data

    monkeypatch.setattr(EncodedStreamObject, 'get_data', observe)
    try:
        with pytest.raises(ParseError, match='PDF.*累计'):
            parse(path, '.pdf')
    finally:
        assert decoded_bytes <= 32 * 1024 * 1024, 'Cumulative decoded content exceeded the budget'


def test_pdf_normal_text_and_configuration_are_preserved(tmp_path):
    path = tmp_path / 'normal.pdf'
    path.write_bytes(pdf_bytes([TEXT, TEXT]))
    before = get_configuration()
    result = parse(path, '.pdf')
    assert result['text'] == '[第 1 页]\nhello\n\n[第 2 页]\nhello'
    assert get_configuration() == before


@pytest.mark.parametrize('restriction', ['encrypted', 'pages'])
def test_pdf_keeps_encryption_and_page_count_restrictions(tmp_path, restriction):
    path = tmp_path / 'restricted.pdf'
    writer = PdfWriter()
    for _ in range(41 if restriction == 'pages' else 1):
        writer.add_blank_page(width=100, height=100)
    if restriction == 'encrypted':
        writer.encrypt('test-password')
    writer.write(path)
    with pytest.raises(ParseError, match='40|加密'):
        parse(path, '.pdf')


def test_docx_checks_declared_body_size_before_opening_it(tmp_path, monkeypatch):
    path = tmp_path / 'large.docx'
    path.write_bytes(docx_bytes(document(paragraph('x' * (8 * 1024 * 1024 + 1)))))
    original = zipfile.ZipFile.open
    opened = []

    def observe(archive, member, *args, **kwargs):
        opened.append(member.filename if isinstance(member, zipfile.ZipInfo) else member)
        return original(archive, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', observe)
    with pytest.raises(ParseError, match='DOCX.*正文'):
        parse(path, '.docx')
    assert 'word/document.xml' not in opened


def test_docx_rejects_duplicate_members_before_reading(tmp_path):
    path = tmp_path / 'duplicate.docx'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', document(paragraph('first')))
            archive.writestr('word/document.xml', document(paragraph('second')))
    with pytest.raises(ParseError, match='DOCX.*重复'):
        parse(path, '.docx')


def test_docx_text_limit_stops_reading_remaining_xml(tmp_path, monkeypatch):
    path = tmp_path / 'long.docx'
    body = document(paragraph('x' * 16001) + '<!--' + 'padding' * 100000 + '-->')
    path.write_bytes(docx_bytes(body))
    original = zipfile.ZipExtFile.read
    reads = []

    def observe(member, n=-1):
        data = original(member, n)
        reads.append((n, len(data)))
        return data

    monkeypatch.setattr(zipfile.ZipExtFile, 'read', observe)
    with pytest.raises(ParseError, match='16000'):
        parse(path, '.docx')
    assert all(n >= 0 for n, _ in reads), 'Unbounded DOCX member read'
    assert sum(size for _, size in reads) < len(body.encode()), 'Remaining XML was read after text limit'


@pytest.mark.parametrize('text', ['normal text', '中' * 16000, ' ' * 20000 + 'hello' + ' ' * 20000],
                         ids=['short', 'at-limit', 'edge-whitespace'])
def test_docx_preserves_text_boundary_and_edge_whitespace(tmp_path, text):
    path = tmp_path / 'normal.docx'
    path.write_bytes(docx_bytes(document(paragraph(text))))
    assert parse(path, '.docx')['text'] == text.strip()


def test_docx_preserves_paragraphs_runs_and_tables(tmp_path):
    path = tmp_path / 'structure.docx'
    body = '<w:p><w:r><w:t>first</w:t></w:r><w:r><w:t> line</w:t></w:r></w:p>'
    body += '<w:tbl><w:tr><w:tc>' + paragraph('cell') + '</w:tc></w:tr></w:tbl>'
    body += paragraph('') + paragraph('last')
    path.write_bytes(docx_bytes(document(body)))
    assert parse(path, '.docx')['text'] == 'first line\ncell\n\nlast'


@pytest.mark.parametrize('kind', ['pdf', 'docx'])
def test_failed_document_upload_stays_private_and_can_be_cleaned(logged_in, app, kind):
    if kind == 'pdf':
        data = pdf_bytes([TEXT + b'%' + b'a' * (8 * 1024 * 1024) + b'\n'])
    else:
        data = docx_bytes(document(paragraph('x' * 16001)))
    response = logged_in.post('/api/attachments', files={'file': (f'large.{kind}', data)})
    assert response.status_code == 201
    item = response.json()
    assert item['status'] == 'failed' and item['error_message']
    assert logged_in.get(f"/api/attachments/{item['id']}/content").status_code == 409
    assert logged_in.delete(f"/api/attachments/{item['id']}").status_code == 204
    with app.state.auth_store.connect() as db:
        assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0
    assert not app.state.attachments.storage.path(item['id']).exists()

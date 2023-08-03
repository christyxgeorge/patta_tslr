import requests
import lxml.html
from PIL import Image
from io import BytesIO
import pytesseract
from bs4 import BeautifulSoup
import xmltodict, json
import re
from itertools import product
from decimal import Decimal
import urllib
import argparse
import sqlite3
from xhtml2pdf import pisa             # import python module
from contextlib import closing

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

PATTA_CHECK_URL = 'https://eservices.tn.gov.in/eservicesnew/land/chittaCheckNewRural_en.html?lan=en'
PATTA_EXTRACT_URL = 'https://eservices.tn.gov.in/eservicesnew/land/chittaExtract_en.html?lan=en'
ESERVICES_URL = "https://eservices.tn.gov.in/eservicesnew/land/ajax.html"
STRIP_HTML_REGEX = re.compile('<.*?>')
STRIP_WSPACE_REGEX = re.compile('\s+', re.UNICODE)

def table_to_2d(table_tag):
    rowspans = []  # track pending rowspans
    rows = table_tag.find_all('tr')

    # first scan, see how many columns we need
    colcount = 0
    for r, row in enumerate(rows):
        cells = row.find_all(['td', 'th'], recursive=False)
        # count columns (including spanned).
        # add active rowspans from preceding rows
        # we *ignore* the colspan value on the last cell, to prevent
        # creating 'phantom' columns with no actual cells, only extended
        # colspans. This is achieved by hardcoding the last cell width as 1.
        # a colspan of 0 means “fill until the end” but can really only apply
        # to the last cell; ignore it elsewhere.
        colcount = max(
            colcount,
            sum(int(c.get('colspan', 1)) or 1 for c in cells[:-1]) + len(cells[-1:]) + len(rowspans))
        # update rowspan bookkeeping; 0 is a span to the bottom.
        rowspans += [int(c.get('rowspan', 1)) or len(rows) - r for c in cells]
        rowspans = [s - 1 for s in rowspans if s > 1]

    # it doesn't matter if there are still rowspan numbers 'active'; no extra
    # rows to show in the table means the larger than 1 rowspan numbers in the
    # last table row are ignored.

    # build an empty matrix for all possible cells
    table = [[None] * colcount for row in rows]

    # fill matrix from row data
    rowspans = {}  # track pending rowspans, column number mapping to count
    for row, row_elem in enumerate(rows):
        span_offset = 0  # how many columns are skipped due to row and colspans
        for col, cell in enumerate(row_elem.find_all(['td', 'th'], recursive=False)):
            # adjust for preceding row and colspans
            col += span_offset
            while rowspans.get(col, 0):
                span_offset += 1
                col += 1

            # fill table data
            rowspan = rowspans[col] = int(cell.get('rowspan', 1)) or len(rows) - row
            colspan = int(cell.get('colspan', 1)) or colcount - col
            # next column is offset by the colspan
            span_offset += colspan - 1
            value = cell.get_text()
            for drow, dcol in product(range(rowspan), range(colspan)):
                try:
                    table[row + drow][col + dcol] = value
                    rowspans[col + dcol] = rowspan
                except IndexError:
                    # rowspan or colspan outside the confines of the table
                    pass

        # update rowspan bookkeeping
        rowspans = {c: s - 1 for c, s in rowspans.items() if s > 1}

    return table

def get_code(session, key, **kwargs):
    qstring=urllib.parse.urlencode(kwargs)
    url = f"{ESERVICES_URL}?{qstring}&lang=en"
    response = session.get(url, verify=False)
    # print(tsnum_response.text)
    resp_json = json.loads(response.text)
    resp_codes = { v['value']: v['name'] for v in resp_json['landrecords']['response'] if v['name'] != '00' }
    return resp_codes.get(key)

def get_subdivision_numbers(session, **kwargs):
    qstring=urllib.parse.urlencode(kwargs)
    url = f"{ESERVICES_URL}?{qstring}"
    response = requests.get(url, verify=False)
    # print(tsnum_response.text)
    xpars = xmltodict.parse(response.text)
    xpars_json = json.loads(json.dumps(xpars))
    if type(xpars_json['root']['subdiv']) is dict:
        subdivNos = [ xpars_json['root']['subdiv']['subdivcode'] ]
    else:
        subdivNos = [ v['subdivcode'] for v in xpars_json['root']['subdiv'] ]
    return subdivNos

def get_captcha_value(session, identifier, debug=False):
    captcha_value = get_captcha_value_internal(session)
    while not validate_captcha(captcha_value, identifier, debug=debug):
        captcha_value = get_captcha_value_internal(session)
    return captcha_value

def validate_captcha(captcha_value, identifier, debug=False):
    if len(captcha_value) != 6:
        if debug: print(f"Invalid Captcha {identifier} - {captcha_value} [Length != 6]")
        return False
    # Seems like Only Alphanumeric is allowed!
    # if bool(re.match(r'^[A-Z]+$', captcha_value)):
    #     print(f"Invalid Captcha {identifier} - {captcha_value} [Only Alphabetic]")
    #     return True
    # Should not be only numeric
    if bool(re.match(r'^[0-9]+$', captcha_value)):
        if debug: print(f"Invalid Captcha {identifier} - {captcha_value} [Only Numeric]")
        return False
    # Valid charset = [0-9A-Z] (no lower case).
    if not bool(re.match(r'^[A-Z0-9]+$', captcha_value)):
        if debug: print(f"Invalid Captcha {identifier} - {captcha_value} [Not Alphanumeric]")
        return False
    return True

def get_captcha_value_internal(session):
    captcha = session.get('https://eservices.tn.gov.in/eservicesnew/land/simpleCaptcha.html', verify=False)
    img = Image.open(BytesIO(captcha.content))
    # gray = img.convert('L')
    # bw = gray.point(lambda x: 0 if x < 1 else 255, '1')
    # img.show()
    captcha_value = pytesseract.image_to_string(img).strip()
    return captcha_value

def get_extract_payload(subdiv_code, captcha_value, **kwargs):
    return {
        'task': 'chittaEng',
        'role': None,
        'viewOption': 'view',
        'districtCode': kwargs['districtCode'],
        'talukCode': kwargs['talukCode'],
        'villageCode': kwargs['villageCode'],
        'viewOpt': 'sur',
        'pattaNo': '',
        'surveyNo': kwargs['surveyno'],
        'subdivNo': subdiv_code,
        'captcha': captcha_value
    }


def get_person_details(table):
    table_array = table_to_2d(table)
    pdetails = {}
    for row in table_array:
        if row[0] and row[0].strip():
            row = [ r.strip().strip('.') for r in row ]
            pdetails[int(row[0])] = ' '.join(row[1:])
    return pdetails

def get_survey_details(table):
    table_array = table_to_2d(table)
    # print(f"Survey Table")
    # for t in table_array: print(f"  {t}")
    table_array = [ l for l in table_array if any(v is not None for v in l) ]
    # headers = [ r.strip() for r in table_array[0] ]
    # headers = [ (h + ' ' + h1.strip()) if h1.strip() else h for h,h1 in zip(headers, table_array[1]) ]
    # headers = [ (h + ' (' + h2.strip() + ')') if h2.strip() else h for h,h2 in zip(headers, table_array[2]) ]
    # print(f"Tamil Headers: {headers}")

    ### Note: Nanjai = Wetland, Panjai = Dryland
    headers = [ "survey no", "subdivision", "dryland spread", "dryland amount", "wetland spread", "wetland amount", "other spread", "other amount", "details"]

    sdetails = {}
    for row in table_array[2:-1]: ## Remove last row (total)
        row = [ r.strip() for r in row ]
        if row[0]:
            sidx = row[0].strip() if row[1].strip().startswith('-') else row[0].strip() + '/' + row[1].strip()
            sdetails[sidx] = {}
            for idx, col in enumerate(row[2:], 2):
                header_prefix = headers[idx].split()[0]
                if headers[idx].endswith("spread"):
                    hectares = float((col.strip().split('-')[0] or '0').strip())
                    ares = float((col.strip().split('-')[1] or '0').strip())
                    if hectares or ares:
                        sdetails[sidx]['land_type'] = header_prefix
                        sdetails[sidx]['hectares'] = hectares # 2.47 acres
                        sdetails[sidx]['ares'] = ares
                        sdetails[sidx]['cents'] = Decimal(str(hectares * 100 + ares)) * Decimal('2.47')
                elif headers[idx].endswith("amount"):
                    if sdetails[sidx].get('land_type') == header_prefix:
                        sdetails[sidx]['amount'] = col.strip()
                else:
                    sdetails[sidx][headers[idx]] = col.strip()
    return sdetails

def extract_patta_details(identifier, html_text):
    soup = BeautifulSoup(html_text, 'lxml')
    tbl = soup.find('table')
    tables_found = 0
    patta_details = {}
    if tbl:
        tds = tbl.find_all('td')
        for idx, td in enumerate(tds):
            if td.find('table'):
                tables_found += 1
                if (tables_found == 1):
                    pdetails = get_person_details(td.find('table'))
                    patta_details['people'] = pdetails
                elif (tables_found == 2):
                    sdetails = get_survey_details(td.find('table'))
                    patta_details['survey'] = sdetails
            elif td and td.contents:
                contents = [ re.sub(STRIP_WSPACE_REGEX, ' ', re.sub(STRIP_HTML_REGEX, '', str(sx).strip())) \
                    for sx in td.contents  if 'பட்டா எண் :' in str(sx) ]
                if contents:
                    patta_number = contents[0].split()[-1]
                    patta_details['patta_number'] = patta_number

        return patta_details
    else:
        form = soup.find("form", {"name": "landForm"})
        if form:
            error =  soup.find("font", {"class": "normal_text_red"})
            print(f"  Error: {error.get_text(strip=True)}")
        else:
            print(f"  Error: Survey Table Not Found")
    # By Default.... return None
    return None

def create_patta_pdf(identifier, html_text):
    #### NOTE: This does not work!
    result_file = open("patta.pdf", "w+b")

    # convert HTML to PDF
    pisa_status = pisa.CreatePDF(
            html_text,                  # the HTML to convert
            dest=result_file)           # file handle to recieve result

    # close output file
    result_file.close()                 # close output file
    print(f"Survey {identifier}: Creating Patta PDF // {pisa_status}")

    # return False on success and True on errors
    return pisa_status.err

def get_patta_details(session, identifier, subdiv_code, **kwargs):
    patta_details = select_patta_details(identifier)
    if patta_details:
        print(f'Survey {identifier}: Found in Sqlite')
    else:
        captcha_value = get_captcha_value(session, identifier)
        payload = get_extract_payload(subdiv_code, captcha_value, **kwargs)
        # print(f'Captcha Text = [{captcha_value}] // Payload = {payload}')
        final_response = session.post(PATTA_EXTRACT_URL, data=payload, verify=False)
        print(f'Survey {identifier}: Response Status = {final_response.status_code}')
        patta_details = extract_patta_details(identifier, final_response.text)
        if patta_details: insert_patta_details(patta_details)
    return patta_details


def print_patta_details(patta_details):
    print(f"  Patta Number: {patta_details['patta_number']}")
    print(f"  Person Details:")
    for k, p in patta_details['people'].items(): print(f"    {k}: {p}")
    print(f"  Survey Details:")
    for k, s in patta_details['survey'].items(): print(f"    {k}: {s}")

def initialize_sqlite_db():
    with closing(sqlite3.connect('patta.db', detect_types=sqlite3.PARSE_DECLTYPES)) as conn:
        with closing(conn.cursor()) as cursor:
            result = cursor.execute('''
              CREATE TABLE IF NOT EXISTS patta_survey_details
              (
                survey_identifier  TEXT PRIMARY KEY  NOT NULL,
                patta_number       INT NOT NULL,
                land_type          VARCHAR(10)       NOT NULL,
                hectares           DECIMAL(10,2)     NOT NULL,
                ares               DECIMAL(10,2)     NOT NULL,
                cents              DECIMAL(10,2)     NOT NULL,
                amount             DECIMAL(10,2)     NOT NULL,
                details            VARCHAR(256),
                people             TEXT
              );
            ''')
            conn.commit()
            # print(f"Created Table: patta_survey_details {result}")

def select_patta_details(survey_identifier):
    def dict_factory(cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
    with closing(sqlite3.connect('patta.db')) as conn:
        conn.row_factory = dict_factory
        with closing(conn.cursor()) as cursor:
            cursor.execute("""
                SELECT * from patta_survey_details
                WHERE patta_number in (SELECT patta_number from patta_survey_details WHERE survey_identifier = ?)
            """, (survey_identifier,))
            rows = cursor.fetchall()
            patta_details = { 'survey': {} }
            for row in rows:
                row_sidx = row['survey_identifier']
                patta_details['patta_number'] = row['patta_number'] # Should be same for all rows!
                patta_details['survey'][row_sidx] = { k: v for k,v in row.items() if k not in {'survey_identifier', 'patta_number' } }
                patta_details['people'] = json.loads(row['people']) # Should be same for all rows!
            return patta_details if len(patta_details['survey']) else None
    return None

def insert_patta_details(patta_details):
    data = []
    for sidx, s in patta_details['survey'].items():
        sdetails = {
            'patta_number': patta_details['patta_number'],
            'survey_identifier': sidx,
        }
        sdetails.update(s)
        sdetails['cents'] = str(sdetails['cents'])
        sdetails['people'] = json.dumps(patta_details['people'])
        data.append(sdetails)
    # print(f"Patta Data = {data}")
    with closing(sqlite3.connect('patta.db')) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.executemany("""
                INSERT INTO patta_survey_details VALUES(:survey_identifier, :patta_number, :land_type,
                    :hectares, :ares, :cents, :amount, :details, :people
                )""", data)
            conn.commit()

def parse_commandline_params():
    def list_str(values):  ### Type in argparse to convert string to list!
        return values.split(',')

    parser = argparse.ArgumentParser(
        prog='Extract Patta',
        description='Extract Patta for a given survey, subdivision (optional)'
    )
    parser.add_argument("-d", "--district", action='store', dest='district_name', default='Tirunelveli', help="Name of the District")
    parser.add_argument("-t", "--taluk", action='store', dest='taluk_name', default='Palayamkottai', help="Name of the Taluk")
    parser.add_argument("-v", "--village", action='store', dest='village_name', default='Tharuvai', help="Name of the Village")
    parser.add_argument("-s", "--survey", required=True, action='store', dest='survey_no', help="Survey Number")
    parser.add_argument("--sdiv", dest='sub_division', type=list_str, help="Comma Separated Subdivision Numbers")
    parser.add_argument("--pdf", action='store_true', dest='create_pdf', default=False, help="Create a PDF of the Patta")
    return parser.parse_args()

#---------------------------------------------------------------------------------
# Main Logic Begins here...
#---------------------------------------------------------------------------------
if __name__ == "__main__":
    args = parse_commandline_params()
    print(f"Args = {args}")
    if not args.district_name:
        print('District Name is Mandatory')
        exit(-1)
    initialize_sqlite_db()
    with requests.session() as s:
        s.headers.update({'referer': 'https://eservices.tn.gov.in/'})
        kwargs = { 'page': 'ruralservice', 'ser': 'dist'}
        district_code = get_code(s, args.district_name, **kwargs)
        kwargs['ser'] = 'tlk'; kwargs['distcode'] = district_code
        taluk_code = get_code(s, args.taluk_name, **kwargs)
        kwargs['ser'] = 'vill'; kwargs['talukcode'] = taluk_code
        village_code = get_code(s, args.village_name, **kwargs)
        print(f"Village Code = {args} // {district_code} // {taluk_code} // {village_code}")
        kwargs = { 'page': 'getSubdivNo', 'districtCode': district_code, 'talukCode': taluk_code, 'villageCode': village_code, 'surveyno': args.survey_no}
        sdiv_nos = get_subdivision_numbers(s, **kwargs)
        print(f"Subdivision Codes for {args.survey_no} is {len(sdiv_nos)} // {sdiv_nos}")
        if args.sub_division:
            sdiv_nos = list(set(args.sub_division).intersection(set(sdiv_nos)))
        for sdiv in sdiv_nos:
            identifier = f"{kwargs['surveyno']}/{sdiv}" if sdiv != '0' else f"{kwargs['surveyno']}"
            patta_details = get_patta_details(s, identifier, sdiv, pdf=args.create_pdf, **kwargs)
            print_patta_details(patta_details)
        print("All Completed!")

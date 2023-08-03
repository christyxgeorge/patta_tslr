import requests
from bs4 import BeautifulSoup
import lxml.html
from PIL import Image
from io import BytesIO
import pytesseract
import xmltodict, json
import re
from itertools import product

ESERVICES_URL = "https://eservices.tn.gov.in/eservicesnew/land/ajax.html"

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def get_url(s, url):
    return s.get(url, verify=False)

def get_form_controls(html):
    tree = lxml.html.fromstring(html)
    data = {}
    for e in tree.cssselect('.form-control'):
      if e.get('name'):
         data[e.get('name')] = e.get('value')
    return data

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

def get_district_codes(html):
    tree = lxml.html.fromstring(html)
    district_dict = {}
    for e in tree.cssselect('option'):
      text = ''.join(e.itertext()).strip()
      # print(f'Value: {str(e)} / {e.get("value")} // {text}')
      if e.get("value"):
          district_dict[text] = str(e.get("value"));

    print(f'District Codes = {district_dict}')
    return district_dict

def get_payload(payload):
    payload['task'] = 'chittaEng';
    payload['districtCode'] = '29' ## Tirunelveli
    payload['talukCode'] = '02'    ## Palayamkotta
    payload['villageCode'] = '003' ## Melapalayam
    # payload['wardNo'] = '013'      ## BM
    return payload

def get_ward_numbers(payload):
    url = f"{ESERVICES_URL}?page=getWard&districtCode={payload['districtCode']}&talukCode={payload['talukCode']}&villageCode={payload['villageCode']}"
    response = requests.get(url, verify=False)
    # print(tsnum_response.text)
    xpars = xmltodict.parse(response.text)
    xpars_json = json.loads(json.dumps(xpars))
    blockCodes = [ v['wardCode'] for v in xpars_json['root']['ward'] ]
    return blockCodes

def get_block_codes(payload):
    url = f"{ESERVICES_URL}?page=getBlocks&districtCode={payload['districtCode']}&talukCode={payload['talukCode']}&villageCode={payload['villageCode']}&wardNo={payload['wardNo']}"
    response = requests.get(url, verify=False)
    # print(tsnum_response.text)
    xpars = xmltodict.parse(response.text)
    xpars_json = json.loads(json.dumps(xpars))
    blockCodes = [ v['blockCode'] for v in xpars_json['root']['block'] ]
    return blockCodes

def get_survey_nos(payload):
    url = f"{ESERVICES_URL}?page=getUrTalSurveyNo&districtCode={payload['districtCode']}&talukCode={payload['talukCode']}&villageCode={payload['villageCode']}&wardCode={payload['wardNo']}&blockCode={payload['blockCode']}"
    response = requests.get(url, verify=False)
    # print(tsnum_response.text)
    xpars = xmltodict.parse(response.text)
    xpars_json = json.loads(json.dumps(xpars))
    surveyNos = [ v['surveyNo'] for v in xpars_json['root']['survey'] ]
    return surveyNos

def get_subdivision_numbers(payload):
    url = f"{ESERVICES_URL}?page=getUrbanTalukSubdivNo&districtCode={payload['districtCode']}&talukCode={payload['talukCode']}&villageCode={payload['villageCode']}&wardCode={payload['wardNo']}&blockCode={payload['blockCode']}&surveyno={payload['surveyNo']}"
    response = requests.get(url, verify=False)
    # print(tsnum_response.text)
    xpars = xmltodict.parse(response.text)
    xpars_json = json.loads(json.dumps(xpars))
    if type(xpars_json['root']['subdiv']) is dict:
        subdivNos = [ xpars_json['root']['subdiv']['subdivcode'] ]
    else:
        subdivNos = [ v['subdivcode'] for v in xpars_json['root']['subdiv'] ]
    return subdivNos

def get_captcha_value(s, payload):
    identifier = get_identifier(payload)
    captcha_value = get_captcha_value_internal(s)
    while not validate_captcha(captcha_value, identifier):
        captcha_value = get_captcha_value_internal(s)
    return captcha_value

def validate_captcha(captcha_value, identifier):
    if len(captcha_value) != 6:
        print(f"Invalid Captcha {identifier} - {captcha_value} [Length != 6]")
        return False
    # Seems like Only Alphanumeric is allowed!
    # if bool(re.match(r'^[A-Z]+$', captcha_value)):
    #     print(f"Invalid Captcha {identifier} - {captcha_value} [Only Alphabetic]")
    #     return True
    # Should not be only numeric
    if bool(re.match(r'^[0-9]+$', captcha_value)):
        print(f"Invalid Captcha {identifier} - {captcha_value} [Only Numeric]")
        return False
    # Valid charset = [0-9A-Z] (no lower case).
    if not bool(re.match(r'^[A-Z0-9]+$', captcha_value)):
        print(f"Invalid Captcha {identifier} - {captcha_value} [Not Alphanumeric]")
        return False
    return True

def get_captcha_value_internal(s):
    captcha = get_url(s, 'https://eservices.tn.gov.in/eservicesnew/land/simpleCaptcha.html')
    img = Image.open(BytesIO(captcha.content))
    # gray = img.convert('L')
    # bw = gray.point(lambda x: 0 if x < 1 else 255, '1')
    # img.show()
    captcha_value = pytesseract.image_to_string(img).strip()
    return captcha_value

def get_identifier(payload):
    return f"[W{payload['wardNo']}/B{payload['blockCode']}/S{payload['surveyNo']}/{payload['subdivNo']}]"


def get_details(s, payload, retry=False):
    identifier = get_identifier(payload)
    captcha_value = get_captcha_value(s, payload)
    payload['captcha'] = captcha_value
    # print(f'Captcha Text = [{captcha_value}] // Payload = {payload}')
    tslr_extract_url = 'https://eservices.tn.gov.in/eservicesnew/land/chittaExtractUrbanTaluk_en.html?lan=en'
    final_response = s.post(tslr_extract_url, data=payload, verify=False)
    # print(f'Final Response Status = {final_response.status_code}')

    soup = BeautifulSoup(final_response.text, 'lxml')
    if soup.find('tbody'):
        tds = soup.find("tbody").find_all("td")
        # Load table from td and th!
        # tables = soup.find_all('table')
        # table_tag = [ table for table in tables if table.find('thead')]
        # print(f'{len(table_tag)} table(s) found with thead')
        # if table_tag:
        #     table = table_to_2d(table_tag[0])
        details = {
            'block_code': tds[1].get_text().strip(),
            'old_survey_no': tds[4].get_text().strip(),
            'door_no': tds[5].get_text().strip(),
            'land_type': tds[6].get_text().strip(),
            'land_sub_type': tds[7].get_text().strip(),
            'addl_details': tds[20].get_text().strip(),
            'remarks': tds[22].get_text().strip()
        }
        print(f"Survey Number {identifier} = {details}")
        return True
    elif retry == True:
        # print log only if retry is True
        details = { 'error': 'not_found', 'captcha': captcha_value, 'status': final_response.status_code }
        print(f"Survey Number {identifier} = {details}")
    # By Default.... return False
    return False

#---------------------------------------------------------------------------------
# Main Logic Begins here...
#---------------------------------------------------------------------------------
if __name__ == "__main__":

    with requests.session() as s:
        tslr_url = 'https://eservices.tn.gov.in/eservicesnew/land/chittaCheckNewUrban_en.html?lan=en'
        response = get_url(s, tslr_url)
        # print(response.status_code)
        # print(s.cookies.get_dict())

        s.headers.update({'referer': 'https://eservices.tn.gov.in/'})
        # surveyNos = [9]
        payload = get_payload({})

        wardNumbers = get_ward_numbers(payload)
        print(f"Number of Ward Numbers: {len(wardNumbers)}")

        # wardNumbers = ['010', '012', '013', '015'] # All wards for KULAVANIGARPURAM
        wardNumbers = [ '013' ]
        for wardNumber in wardNumbers:
            payload['wardNo'] = wardNumber
            blockCodes = get_block_codes(payload) # blockCode = '0014' ('A2') or '0011' (B233)
            print(f"Number of Block Codes in Ward [W{wardNumber}]: {len(blockCodes)}")
            # blockCodes = ['0011', '0014']
            for blockCode in blockCodes:
                payload['blockCode'] = blockCode
                surveyNos = get_survey_nos(payload)
                print(f"Number of Survey Numbers in Block [W{wardNumber}/B{blockCode}]: {len(surveyNos)}")
                for surveyNo in surveyNos:
                    payload['surveyNo'] = surveyNo
                    subdiv_nos = get_subdivision_numbers(payload)
                    for subdivNo in subdiv_nos:
                        payload['subdivNo'] = subdivNo
                        retval = get_details(s, payload)
                        if not retval: retval = get_details(s, payload, retry=True) ## Try again
                        if not retval:
                            identifier = get_identifier(payload)
                            print(f"Unable to get details for {identifier}")

        print("All Completed!")

#!/usr/local/bin/python3.5

import csv
import glob
import os
import re
import urllib.request, urllib.error, urllib.parse
import calendar
import sys
import subprocess
import json
import codecs
import numpy
import resource
from cdecimal import Decimal
from astroquery.vizier import Vizier
from astropy.time import Time as astrotime
from astropy.cosmology import Planck15 as cosmo
from collections import OrderedDict
from math import log10, floor, sqrt, isnan
from bs4 import BeautifulSoup, SoupStrainer, Tag, NavigableString
from operator import itemgetter
from string import ascii_letters

clight = 29979245800.

eventnames = []

dovizier =         True
dosuspect =        True
docfa =            True
doucb =            True
dosdss =           True
dogaia =           True
docsp =            True
doitep =           True
doasiago =         True
dorochester =      True
dofirstmax =       True
dolennarz =        True
doogle =           True
donedd =           True
docfaiaspectra =   True
docfaibcspectra =  True
dosnlsspectra =    True
docspspectra =     True
doucbspectra =     True
dosuspectspectra = True
writeevents =      True
printextra =       False

events = OrderedDict()

with open('rep-folders.txt', 'r') as f:
    repfolders = f.read().splitlines()

repyears = [int(repfolders[x][-4:]) for x in range(len(repfolders))]
repyears[0] -= 1

typereps = {
    'I P':    ['I pec', 'I-pec', 'I Pec', 'I-Pec'],
    'Ia P':   ['Ia pec', 'Ia-pec', 'Iapec'],
    'Ib P':   ['Ib pec', 'Ib-pec'],
    'Ic P':   ['Ic pec', 'Ic-pec'],
    'Ib/c':   ['Ibc'],
    'Ib/c P': ['Ib/c-pec'],
    'II P':   ['II pec', 'IIpec', 'II Pec', 'IIPec', 'IIP', 'IIp', 'II p', 'II-pec', 'II P pec', 'II-P'],
    'II L':   ['IIL'],
    'IIn P':  ['IIn pec', 'IIn-pec'],
    'IIb P':  ['IIb-pec', 'IIb: pec']
}

repbetterquanta = {
    'redshift',
    'ebv',
    'hvel',
    'lumdist'
}

def event_attr_priority(attr):
    if attr == 'photometry' or attr == 'spectra':
        return 'zzzzzzzz'
    if attr == 'name':
        return 'aaaaaaaa'
    if attr == 'aliases':
        return 'aaaaaaab'
    if attr == 'sources':
        return 'aaaaaaac'
    return attr

def add_event(name):
    if name not in events:
        for event in events:
            if len(events[event]['aliases']) > 1 and name in events[event]['aliases']:
                return event
        print(name)
        events[name] = OrderedDict()
        events[name]['name'] = name
        add_alias(name, name)
        return name
    else:
        return name

def event_filename(name):
    return(name.replace('/', '_'))

def add_alias(name, alias):
    if 'aliases' in events[name]:
        if alias not in events[name]['aliases']:
            events[name].setdefault('aliases',[]).append(alias)
    else:
        events[name]['aliases'] = [alias]

def snname(string):
    newstring = string.replace(' ', '').upper()
    if (newstring[:2] == "SN"):
        head = newstring[:6]
        tail = newstring[6:]
        if len(tail) >= 2 and tail[1] != '?':
            tail = tail.lower()
        newstring = head + tail

    return newstring

def get_sig_digits(x):
    return len((''.join(x.split('.'))).strip('0'))

def round_sig(x, sig=4):
    if x == 0.0:
        return 0.0
    return round(x, sig-int(floor(log10(abs(x))))-1)

def pretty_num(x, sig=4):
    return str('%g'%(round_sig(x, sig)))

def get_source(name, reference = '', url = '', bibcode = '', secondary = ''):
    nsources = len(events[name]['sources']) if 'sources' in events[name] else 0
    if not reference:
        if not bibcode:
            raise(ValueError('Bibcode must be specified if name is not.'))
        else:
            if url:
                print('Warning: Reference URL ignored if bibcode specified')
        reference = bibcode
        url = "http://adsabs.harvard.edu/abs/" + bibcode
    if 'sources' not in events[name] or reference not in [events[name]['sources'][x]['name'] for x in range(nsources)]:
        source = str(nsources + 1)
        newsource = OrderedDict()
        newsource['name'] = reference
        if url:
            newsource['url'] = url
        if bibcode:
            newsource['bibcode'] = bibcode
        newsource['alias'] =  source
        if secondary:
            newsource['secondary'] = True
        events[name].setdefault('sources',[]).append(newsource)
    else:
        sourcexs = range(nsources)
        source = [events[name]['sources'][x]['alias'] for x in sourcexs][
            [events[name]['sources'][x]['name'] for x in sourcexs].index(reference)]
    return source

def add_photometry(name, timeunit = "MJD", time = "", instrument = "", band = "", abmag = "", aberr = "", source = "", upperlimit = False):
    if not time or not abmag:
        print('Warning: Time or AB mag not specified when adding photometry.\n')
        print('Name : "' + name + '", Time: "' + time + '", Band: "' + band + '", AB mag: "' + abmag + '"')
        return

    if not is_number(time) or not is_number(abmag):
        print('Warning: Time or AB mag not numerical.\n')
        print('Name : "' + name + '", Time: "' + time + '", Band: "' + band + '", AB mag: "' + abmag + '"')
        return

    if aberr and not is_number(aberr):
        print('Warning: AB error not numerical.\n')
        print('Name : "' + name + '", Time: "' + time + '", Band: "' + band + '", AB err: "' + aberr + '"')
        return

    # Look for duplicate data and don't add if duplicate
    if 'photometry' in events[name]:
        for photo in events[name]['photometry']:
            if (photo['timeunit'] == timeunit and photo['band'] == band and
                Decimal(photo['time']) == Decimal(time) and
                Decimal(photo['abmag']) == Decimal(abmag) and
                (('aberr' not in photo and not aberr) or ('aberr' in photo and aberr and Decimal(photo['aberr']) == Decimal(aberr)) or
                ('aberr' in photo and not aberr))):
                return

    photoentry = OrderedDict()
    photoentry['timeunit'] = timeunit
    photoentry['time'] = str(time)
    photoentry['band'] = band
    photoentry['abmag'] = str(abmag)
    if instrument:
        photoentry['instrument'] = instrument
    if aberr:
        photoentry['aberr'] = str(aberr)
    if source:
        photoentry['source'] = source
    if upperlimit:
        photoentry['upperlimit'] = upperlimit
    events[name].setdefault('photometry',[]).append(photoentry)

def add_spectrum(name, waveunit, fluxunit, wavelengths, fluxes, timeunit = "", time = "", instrument = "",
    deredshifted = "", dereddened = "", errorunit = "", errors = "", source = "", snr = "",
    observer = "", reducer = ""):
    if not waveunit:
        'Warning: No error unit specified, not adding spectrum.'
        return
    if not fluxunit:
        'Warning: No flux unit specified, not adding spectrum.'
        return
    spectrumentry = OrderedDict()
    if deredshifted != '':
        spectrumentry['deredshifted'] = deredshifted
    if dereddened != '':
        spectrumentry['dereddened'] = dereddened
    if instrument:
        spectrumentry['instrument'] = instrument
    if timeunit:
        spectrumentry['timeunit'] = timeunit
    if time:
        spectrumentry['time'] = time
    if snr:
        spectrumentry['snr'] = snr
    if observer:
        spectrumentry['observer'] = observer
    if reducer:
        spectrumentry['reducer'] = reducer

    spectrumentry['waveunit'] = waveunit
    spectrumentry['fluxunit'] = fluxunit
    if errors and max([float(x) for x in errors]) > 0.:
        if not errorunit:
            'Warning: No error unit specified, not adding spectrum.'
            return
        spectrumentry['errorunit'] = errorunit
        data = [wavelengths, fluxes, errors]
    else:
        data = [wavelengths, fluxes]
    spectrumentry['data'] = [list(i) for i in zip(*data)]
    if source:
        spectrumentry['source'] = source
    events[name].setdefault('spectra',[]).append(spectrumentry)

def add_quanta(name, quanta, value, source, forcereplacebetter = False, error = ''):
    if not quanta:
        raise(ValueError('Quanta must be specified for add_quanta.'))
    svalue = value.strip()
    serror = error.strip()
    if not svalue or svalue == '--' or svalue == '-':
        return
    if serror and (not is_number(serror) or float(serror) < 0.):
        raise(ValueError('Quanta error value must be a number and positive.'))

    #Handle certain quanta
    if quanta == 'hvel' or quanta == 'redshift':
        if not is_number(value):
            return
    if quanta == 'host':
        svalue = svalue.replace("NGC", "NGC ")
        svalue = svalue.replace("UGC", "UGC ")
        svalue = svalue.replace("IC", "IC ")
        svalue = ' '.join(svalue.split())
    elif quanta == 'claimedtype':
        for rep in typereps:
            if svalue in typereps[rep]:
                svalue = rep
                break

    if is_number(value):
        svalue = '%g' % Decimal(svalue)
    if serror:
        serror = '%g' % Decimal(serror)

    if quanta in events[name]:
        for i, ct in enumerate(events[name][quanta]):
            if ct['value'] == svalue:
                if source and source not in events[name][quanta][i]['source'].split(','):
                    events[name][quanta][i]['source'] += ',' + source
                    if serror:
                        events[name][quanta][i]['error'] = serror
                return

    quantaentry = OrderedDict()
    quantaentry['value'] = svalue
    if serror:
        quantaentry['error'] = serror
    if source:
        quantaentry['source'] = source
    if (forcereplacebetter or quanta in repbetterquanta) and quanta in events[name]:
        newquantas = []
        isworse = True
        newsig = get_sig_digits(svalue)
        for ct in events[name][quanta]:
            if 'error' in ct:
                if serror:
                    if float(serror) < float(ct['error']):
                        isworse = False
                        continue
                newquantas.append(ct)
            else:
                if serror:
                    isworse = False
                    continue
                oldsig = get_sig_digits(ct['value'])
                if oldsig >= newsig:
                    newquantas.append(ct)
                if newsig >= oldsig:
                    isworse = False
        if not isworse:
            newquantas.append(quantaentry)
        events[name][quanta] = newquantas
    else:
        events[name].setdefault(quanta,[]).append(quantaentry)

def get_max_light(name):
    if 'photometry' not in events[name]:
        return (None, None)

    eventphoto = [Decimal(events[name]['photometry'][x]['abmag']) for x in range(len(events[name]['photometry']))]
    mlmag = min(eventphoto)
    mlindex = eventphoto.index(mlmag)
    mlmjd = float(events[name]['photometry'][mlindex]['time'])
    return (astrotime(mlmjd, format='mjd').datetime, mlmag)

def get_first_light(name):
    if 'photometry' not in events[name]:
        return None

    eventtime = [events[name]['photometry'][x]['time'] for x in range(len(events[name]['photometry']))]
    flindex = eventtime.index(min(eventtime))
    flmjd = float(events[name]['photometry'][flindex]['time'])
    return astrotime(flmjd, format='mjd').datetime

def set_first_max_light(name):
    (mldt, mlmag) = get_max_light(name)
    if mldt:
        events[name]['maxyear'] = pretty_num(mldt.year)
        events[name]['maxmonth'] = pretty_num(mldt.month)
        events[name]['maxday'] = pretty_num(mldt.day)
        events[name]['maxappmag'] = pretty_num(mlmag)

    fldt = get_first_light(name)
    if fldt:
        events[name]['discoveryear'] = pretty_num(fldt.year)
        events[name]['discovermonth'] = pretty_num(fldt.month)
        events[name]['discoverday'] = pretty_num(fldt.day)

def jd_to_mjd(jd):
    return jd - Decimal(2400000.5)

def utf8(x):
    return str(x, 'utf-8')

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

catalog = OrderedDict()
def convert_aq_output(row):
    return OrderedDict([(x, str(row[x]) if is_number(row[x]) else row[x]) for x in row.colnames])

# Import primary data sources from Vizier
if dovizier:
    Vizier.ROW_LIMIT = -1
    result = Vizier.get_catalogs("VII/272/snrs")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)

    for row in table:
        row = convert_aq_output(row)
        name = ''
        if row["Names"]:
            names = row["Names"].split(',')
            for nam in names:
                if nam.strip()[:2] == 'SN':
                    name = nam.strip()
            if not name:
                for nam in names:
                    if nam.strip('()') == nam:
                        name = nam.strip()
                        break
        if not name:
            name = row["SNR"]

        name = add_event(name)

        if row["Names"]:
            names = row["Names"].split(',')
            for nam in names:
                if nam.strip()[:2] == 'SN':
                    events[name]['discoveryear'] = nam.strip()[2:]

        events[name]['snra'] = row['RAJ2000']
        events[name]['sndec'] = row['DEJ2000']

    result = Vizier.get_catalogs("J/MNRAS/442/844/table1")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)
    for row in table:
        row = convert_aq_output(row)
        name = 'SN' + row['SN']
        name = add_event(name)
        source = get_source(name, bibcode = '2014MNRAS.442..844F')
        add_quanta(name, 'redshift', row['zhost'], source)
        add_quanta(name, 'ebv', row['E_B-V_'], source)

    result = Vizier.get_catalogs("J/MNRAS/425/1789/table1")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)
    for row in table:
        row = convert_aq_output(row)
        name = ''.join(row['SimbadName'].split(' '))
        name = add_event(name)
        add_alias(name, 'SN' + row['SN'])
        source = get_source(name, bibcode = '2012MNRAS.425.1789S')
        add_quanta(name, 'host', row['Gal'], source)
        add_quanta(name, 'hvel', row['cz'], source)
        add_quanta(name, 'ebv', row['E_B-V_'], source)

    result = Vizier.get_catalogs("J/MNRAS/442/844/table2")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)
    for row in table:
        row = convert_aq_output(row)
        name = 'SN' + str(row['SN'])
        name = add_event(name)
        source = get_source(name, bibcode = "2014MNRAS.442..844F")
        if 'Bmag' in row and is_number(row['Bmag']) and not isnan(float(row['Bmag'])):
            add_photometry(name, time = row['MJD'], band = 'B', abmag = row['Bmag'], aberr = row['e_Bmag'], source = source)
        if 'Vmag' in row and is_number(row['Vmag']) and not isnan(float(row['Vmag'])):
            add_photometry(name, time = row['MJD'], band = 'V', abmag = row['Vmag'], aberr = row['e_Vmag'], source = source)
        if 'Rmag' in row and is_number(row['Rmag']) and not isnan(float(row['Rmag'])):
            add_photometry(name, time = row['MJD'], band = 'R', abmag = row['Rmag'], aberr = row['e_Rmag'], source = source)
        if 'Imag' in row and is_number(row['Imag']) and not isnan(float(row['Imag'])):
            add_photometry(name, time = row['MJD'], band = 'I', abmag = row['Imag'], aberr = row['e_Imag'], source = source)

    result = Vizier.get_catalogs("J/ApJS/219/13/table3")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)
    for row in table:
        row = convert_aq_output(row)
        name = u'LSQ' + str(row['LSQ'])
        name = add_event(name)
        source = get_source(name, bibcode = "2015ApJS..219...13W")
        events[name]['snra'] = row['RAJ2000']
        events[name]['sndec'] = row['DEJ2000']
        add_quanta(name, 'redshift', row['z'], source, error = row['e_z'])
        add_quanta(name, 'ebv', row['E_B-V_'], source)
    result = Vizier.get_catalogs("J/ApJS/219/13/table2")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)
    for row in table:
        row = convert_aq_output(row)
        name = 'LSQ' + row['LSQ']
        source = get_source(name, bibcode = "2015ApJS..219...13W")
        add_photometry(name, time = str(jd_to_mjd(Decimal(row['JD']))), instrument = 'La Silla-QUEST', band = row['Filt'], abmag = row['mag'], aberr = row['e_mag'], source = source)

# Suspect catalog
if dosuspect:
    with open('../external/suspectreferences.csv','r') as f:
        tsvin = csv.reader(f, delimiter='\t', skipinitialspace=True)
        suspectrefdict = {}
        for row in tsvin:
            suspectrefdict[row[0]] = row[1]

    response = urllib.request.urlopen('http://www.nhn.ou.edu/cgi-bin/cgiwrap/~suspect/snindex.cgi')

    soup = BeautifulSoup(response.read(), "html5lib")
    i = 0
    for a in soup.findAll('a'):
        if 'phot=yes' in a['href'] and not 'spec=yes' in a['href']:
            if int(a.contents[0]) > 0:
                i = i + 1
                photlink = 'http://www.nhn.ou.edu/cgi-bin/cgiwrap/~suspect/' + a['href']
                eventresp = urllib.request.urlopen(photlink)
                eventsoup = BeautifulSoup(eventresp, "html5lib")
                ei = 0
                for ea in eventsoup.findAll('a'):
                    if ea.contents[0] == 'I':
                        ei = ei + 1
                        bandlink = 'http://www.nhn.ou.edu/cgi-bin/cgiwrap/~suspect/' + ea['href']
                        bandresp = urllib.request.urlopen(bandlink)
                        bandsoup = BeautifulSoup(bandresp, "html5lib")
                        bandtable = bandsoup.find('table')
                        if ei == 1:
                            names = bandsoup.body.findAll(text=re.compile("Name"))
                            name = 'SN' + names[0].split(':')[1].strip()
                            name = add_event(name)

                            reference = ''
                            for link in bandsoup.body.findAll('a'):
                                if 'adsabs' in link['href']:
                                    reference = str(link).replace('"', "'")

                            bibcode = suspectrefdict[reference]
                            source = get_source(name, bibcode = bibcode)

                            year = re.findall(r'\d+', name)[0]
                            events[name]['discoveryear'] = year
                            add_quanta(name, 'host', names[1].split(':')[1].strip(), source)

                            redshifts = bandsoup.body.findAll(text=re.compile("Redshift"))
                            if redshifts:
                                add_quanta(name, 'redshift', redshifts[0].split(':')[1].strip(), source)
                            hvels = bandsoup.body.findAll(text=re.compile("Heliocentric Velocity"))
                            if hvels:
                                add_quanta(name, 'hvel', hvels[0].split(':')[1].strip().split(' ')[0], source)
                            types = bandsoup.body.findAll(text=re.compile("Type"))

                            add_quanta(name, 'claimedtype', types[0].split(':')[1].strip().split(' ')[0], source)

                        bands = bandsoup.body.findAll(text=re.compile("^Band"))
                        band = bands[0].split(':')[1].strip()

                        secondaryreference = "SUSPECT"
                        secondaryrefurl = "https://www.nhn.ou.edu/~suspect/"
                        secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)

                        for r, row in enumerate(bandtable.findAll('tr')):
                            if r == 0:
                                continue
                            col = row.findAll('td')
                            mjd = str(jd_to_mjd(Decimal(col[0].contents[0])))
                            mag = col[3].contents[0]
                            if mag.isspace():
                                mag = ''
                            else:
                                mag = str(mag)
                            aberr = col[4].contents[0]
                            if aberr.isspace():
                                aberr = ''
                            else:
                                aberr = str(aberr)
                            add_photometry(name, time = mjd, band = band, abmag = mag, aberr = aberr, source = secondarysource + ',' + source)

# CfA data
if docfa:
    for file in sorted(glob.glob("../external/cfa-input/*.dat"), key=lambda s: s.lower()):
        f = open(file,'r')
        tsvin = csv.reader(f, delimiter=' ', skipinitialspace=True)
        csv_data = []
        for r, row in enumerate(tsvin):
            new = []
            for item in row:
                new.extend(item.split("\t"))
            csv_data.append(new)

        for r, row in enumerate(csv_data):
            for c, col in enumerate(row):
                csv_data[r][c] = col.strip()
            csv_data[r] = [_f for _f in csv_data[r] if _f]

        eventname = os.path.basename(os.path.splitext(file)[0])

        eventparts = eventname.split('_')

        name = snname(eventparts[0])
        name = add_event(name)

        year = re.findall(r'\d+', name)[0]
        events[name]['discoveryear'] = year

        eventbands = list(eventparts[1])

        tu = 'MJD'
        jdoffset = Decimal(0.)
        for rc, row in enumerate(csv_data):
            if len(row) > 0 and row[0][0] == "#":
                if len(row[0]) > 2 and row[0][:3] == "#JD":
                    tu = 'JD'
                    rowparts = row[0].split('-')
                    jdoffset = Decimal(rowparts[1])
                elif len(row[0]) > 6 and row[0][:7] == "#Julian":
                    tu = 'JD'
                    jdoffset = Decimal(0.)
                elif len(row) > 1 and row[1].lower() == "photometry":
                    for ci, col in enumerate(row[2:]):
                        if col[0] == "(":
                            refstr = ' '.join(row[2+ci:])
                            refstr = refstr.replace('(','').replace(')','')
                            bibcode = refstr
                            secondaryname = 'CfA Supernova Archive'
                            secondaryurl = 'https://www.cfa.harvard.edu/supernova/SNarchive.html'
                            secondarysource = get_source(name, reference = secondaryname, url = secondaryurl, secondary = True)
                            source = get_source(name, bibcode = bibcode)

                elif len(row) > 1 and row[1] == "HJD":
                    tu = "HJD"

                continue
            elif len(row) > 0:
                mjd = row[0]
                for v, val in enumerate(row):
                    if v == 0:
                        if tu == 'JD':
                            mjd = str(jd_to_mjd(Decimal(val) + jdoffset))
                            tuout = 'MJD'
                        elif tu == 'HJD':
                            mjd = str(jd_to_mjd(Decimal(val)))
                            tuout = 'MJD'
                        else:
                            mjd = val
                            tuout = tu
                    elif v % 2 != 0:
                        if float(row[v]) < 90.0:
                            add_photometry(name, timeunit = tuout, time = mjd, band = eventbands[(v-1)//2], abmag = row[v], aberr = row[v+1], source = secondarysource + ',' + source)
        f.close()

    # Hicken 2012
    f = open("../external/hicken-2012-standard.dat", 'r')
    tsvin = csv.reader(f, delimiter='|', skipinitialspace=True)
    for r, row in enumerate(tsvin):
        if r <= 47:
            continue

        if row[0][:2] != 'sn':
            name = 'SN' + row[0].strip()
        else:
            name = row[0].strip()

        name = add_event(name)

        source = get_source(name, bibcode = '2012ApJS..200...12H')
        add_photometry(name, timeunit = 'MJD', time = row[2].strip(), band = row[1].strip(),
            abmag = row[6].strip(), aberr = row[7].strip(), source = source)
    
    # Bianco 2014
    tsvin = open("../external/bianco-2014-standard.dat", 'r')
    tsvin = csv.reader(tsvin, delimiter=' ', skipinitialspace=True)
    for row in tsvin:
        name = 'SN' + row[0]
        name = add_event(name)

        source = get_source(name, bibcode = '2014ApJS..213...19B')
        add_photometry(name, timeunit = 'MJD', time = row[2], band = row[1], abmag = row[3], aberr = row[4], instrument = row[5], source = source)
    f.close()

# Now import the UCB SNDB
if doucb:
    for file in sorted(glob.glob("../external/SNDB/*.dat"), key=lambda s: s.lower()):
        f = open(file,'r')
        tsvin = csv.reader(f, delimiter=' ', skipinitialspace=True)

        eventname = os.path.basename(os.path.splitext(file)[0])

        eventparts = eventname.split('.')

        name = snname(eventparts[0])
        name = add_event(name)

        year = re.findall(r'\d+', name)[0]
        events[name]['discoveryear'] = year

        reference = "UCB Filippenko Group's Supernova Database (SNDB)"
        refurl = "http://heracles.astro.berkeley.edu/sndb/info"
        source = get_source(name, reference = reference, url = refurl, secondary = True)

        for r, row in enumerate(tsvin):
            if len(row) > 0 and row[0] == "#":
                continue
            mjd = row[0]
            abmag = row[1]
            aberr = row[2]
            band = row[4]
            instrument = row[5]
            add_photometry(name, time = mjd, instrument = instrument, band = band, abmag = abmag, aberr = aberr, source = source)
        f.close()
    
# Import SDSS
if dosdss:
    sdssbands = ['u', 'g', 'r', 'i', 'z']
    for file in sorted(glob.glob("../external/SDSS/*.sum"), key=lambda s: s.lower()):
        f = open(file,'r')
        tsvin = csv.reader(f, delimiter=' ', skipinitialspace=True)

        for r, row in enumerate(tsvin):
            if r == 0:
                if row[5] == "RA:":
                    name = "SDSS" + row[3]
                else:
                    name = "SN" + row[5]
                name = add_event(name)

                if row[5] != "RA:":
                    year = re.findall(r'\d+', name)[0]
                    events[name]['discoveryear'] = year

                events[name]['snra'] = row[-4]
                events[name]['sndec'] = row[-2]

                reference = "SDSS Supernova Survey"
                refurl = "http://classic.sdss.org/supernova/lightcurves.html"
                source = get_source(name, reference = reference, url = refurl)
            if r == 1:
                add_quanta(name, 'redshift', row[2], source, error = row[4])
            if r >= 19:
                # Skip bad measurements
                if int(row[0]) > 1024:
                    continue

                mjd = row[1]
                band = sdssbands[int(row[2])]
                abmag = row[3]
                aberr = row[4]
                instrument = "SDSS"
                add_photometry(name, time = mjd, instrument = instrument, band = band, abmag = abmag, aberr = aberr, source = source)
        f.close()

#Import GAIA
if dogaia:
    #response = urllib2.urlopen('https://gaia.ac.uk/selected-gaia-science-alerts')
    path = os.path.abspath('../external/selected-gaia-science-alerts')
    response = urllib.request.urlopen('file://' + path)
    html = response.read()

    soup = BeautifulSoup(html, "html5lib")
    table = soup.findAll("table")[1]
    for r, row in enumerate(table.findAll('tr')):
        if r == 0:
            continue

        col = row.findAll('td')
        classname = col[7].contents[0]

        if 'SN' not in classname:
            continue

        links = row.findAll('a')
        name = links[0].contents[0]

        if name == 'Gaia15aaaa':
            continue

        name = add_event(name)

        year = '20' + re.findall(r'\d+', name)[0]
        events[name]['discoveryear'] = year

        reference = "Gaia Photometric Science Alerts"
        refurl = "https://gaia.ac.uk/selected-gaia-science-alerts"
        source = get_source(name, reference = reference, url = refurl)

        events[name]['snra'] = col[2].contents[0].strip()
        events[name]['sndec'] = col[3].contents[0].strip()
        add_quanta(name, 'claimedtype', classname.replace('SN', '').strip(), source)

        photlink = 'http://gsaweb.ast.cam.ac.uk/alerts/alert/' + name + '/lightcurve.csv/'
        photresp = urllib.request.urlopen(photlink)
        photsoup = BeautifulSoup(photresp, "html5lib")
        photodata = str(photsoup.contents[0]).split('\n')[2:-1]
        for ph in photodata:
            photo = ph.split(',')
            mjd = str(jd_to_mjd(Decimal(photo[1].strip())))
            abmag = photo[2].strip()
            aberr = 0.
            instrument = 'GAIA'
            band = 'G'
            add_photometry(name, time = mjd, instrument = instrument, band = band, abmag = abmag, aberr = aberr, source = source)

# Import CSP
if docsp:
    cspbands = ['u', 'B', 'V', 'g', 'r', 'i', 'Y', 'J', 'H', 'K']
    for file in sorted(glob.glob("../external/CSP/*.dat"), key=lambda s: s.lower()):
        f = open(file,'r')
        tsvin = csv.reader(f, delimiter='\t', skipinitialspace=True)

        eventname = os.path.basename(os.path.splitext(file)[0])

        eventparts = eventname.split('opt+')

        name = snname(eventparts[0])
        name = add_event(name)

        year = re.findall(r'\d+', name)[0]
        events[name]['discoveryear'] = year

        reference = "Carnegie Supernova Project"
        refurl = "http://csp.obs.carnegiescience.edu/data"
        source = get_source(name, reference = reference, url = refurl)

        for r, row in enumerate(tsvin):
            if len(row) > 0 and row[0][0] == "#":
                if r == 2:
                    add_quanta(name, 'redshift', row[0].split(' ')[-1], source)
                    events[name]['snra'] = row[1].split(' ')[-1]
                    events[name]['sndec'] = row[2].split(' ')[-1]
                continue
            for v, val in enumerate(row):
                if v == 0:
                    mjd = val
                elif v % 2 != 0:
                    if float(row[v]) < 90.0:
                        add_photometry(name, time = mjd, instrument = 'CSP', band = cspbands[(v-1)//2], abmag = row[v], aberr = row[v+1], source = source)
        f.close()

# Import ITEP
if doitep:
    needsbib = []
    with open("../external/itep-refs.txt",'r') as f:
        refrep = f.read().splitlines()
    refrepf = dict(list(zip(refrep[1::2], refrep[::2])))
    f = open("../external/itep-lc-cat-28dec2015.txt",'r')
    tsvin = csv.reader(f, delimiter='|', skipinitialspace=True)
    curname = ''
    for r, row in enumerate(tsvin):
        if r <= 1 or len(row) < 7:
            continue
        name = 'SN' + row[0].strip()
        mjd = str(jd_to_mjd(Decimal(row[1].strip())))
        band = row[2].strip()
        abmag = row[3].strip()
        aberr = row[4].strip()
        reference = row[6].strip().strip(',')

        if curname != name:
            curname = name
            name = add_event(name)
            year = re.findall(r'\d+', name)[0]
            events[name]['discoveryear'] = year

            secondaryreference = "Sternberg Astronomical Institute Supernova Light Curve Catalogue"
            secondaryrefurl = "http://dau.itep.ru/sn/node/72"
            secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)

        if reference in refrepf:
            bibcode = refrepf[reference]
            source = get_source(name, bibcode = bibcode)
        else:
            needsbib.append(reference)
            source = get_source(name, reference = reference) if reference else ''

        add_photometry(name, time = mjd, band = band, abmag = abmag, aberr = aberr, source = secondarysource + ',' + source)
    f.close()
    
    # Write out references that could use a bibcode
    needsbib = list(OrderedDict.fromkeys(needsbib))
    with open('../itep-needsbib.txt', 'w') as f:
        f.writelines(["%s\n" % i for i in needsbib])

# Now import the Asiago catalog
if doasiago:
    #response = urllib.request.urlopen('http://graspa.oapd.inaf.it/cgi-bin/sncat.php')
    path = os.path.abspath('../external/asiago-cat.php')
    response = urllib.request.urlopen('file://' + path)
    html = response.read().decode('utf-8')
    html = html.replace("\r", "")

    soup = BeautifulSoup(html, "html5lib")
    table = soup.find("table")

    records = []
    for r, row in enumerate(table.findAll('tr')):
        if r == 0:
            continue

        col = row.findAll('td')
        records.append([utf8(x.renderContents()) for x in col])

    for record in records:
        if len(record) > 1 and record[1] != '':
            name = snname("SN" + record[1]).strip('?')
            name = add_event(name)

            reference = 'Asiago Supernova Catalogue'
            refurl = 'http://graspa.oapd.inaf.it/cgi-bin/sncat.php'
            source = get_source(name, reference = reference, url = refurl, secondary = True)

            year = re.findall(r'\d+', name)[0]
            events[name]['discoveryear'] = year

            hostname = record[2]
            galra = record[3]
            galdec = record[4]
            snra = record[5]
            sndec = record[6]
            redvel = record[11].strip(':')
            discoverer = record[19]

            datestr = record[18]
            if "*" in datestr:
                monthkey = 'discovermonth'
                daykey = 'discoverday'
            else:
                monthkey = 'maxmonth'
                daykey = 'maxday'

            if datestr.strip() != '':
                dayarr = re.findall(r'\d+', datestr)
                if dayarr:
                    events[name][daykey] = dayarr[0]
                monthstr = ''.join(re.findall("[a-zA-Z]+", datestr))
                events[name][monthkey] = list(calendar.month_abbr).index(monthstr)

            hvel = ''
            redshift = ''
            if redvel != '':
                if round(float(redvel)) == float(redvel):
                    hvel = int(redvel)
                else:
                    redshift = float(redvel)
                redshift = str(redshift)
                hvel = str(hvel)

            claimedtype = record[17].strip(':')

            if (hostname != ''):
                add_quanta(name, 'host', hostname, source)
            if (claimedtype != ''):
                add_quanta(name, 'claimedtype', claimedtype, source)
            if (redshift != ''):
                add_quanta(name, 'redshift', redshift, source)
            if (hvel != ''):
                add_quanta(name, 'hvel', hvel, source)
            if (galra != ''):
                events[name]['galra'] = galra
            if (galdec != ''):
                events[name]['galdec'] = galdec
            if (snra != ''):
                events[name]['snra'] = snra
            if (sndec != ''):
                events[name]['sndec'] = sndec
            if (discoverer != ''):
                events[name]['discoverer'] = discoverer

if dorochester:
    rochesterpaths = ['file://'+os.path.abspath('../external/snredshiftall.html'), 'http://www.rochesterastronomy.org/sn2016/snredshift.html']
    for path in rochesterpaths:
        response = urllib.request.urlopen(path)
        html = response.read()

        soup = BeautifulSoup(html, "html5lib")
        rows = soup.findAll('tr')
        secondaryreference = "Latest Supernovae"
        secondaryrefurl = "http://www.rochesterastronomy.org/snimages/snredshiftall.html"
        for r, row in enumerate(rows):
            if r == 0:
                continue
            cols = row.findAll('td')
            if not len(cols):
                continue

            name = ''
            if cols[14].contents:
                aka = str(cols[14].contents[0]).strip()
                if is_number(aka[:4]):
                    aka = 'SN' + aka
                    name = add_event(aka)

            sn = re.sub('<[^<]+?>', '', str(cols[0].contents[0])).strip()
            if sn[:4].isdigit():
                sn = 'SN' + sn
            if not name:
                name = add_event(sn)

            if cols[14].contents:
                add_alias(name, aka)

            reference = cols[12].findAll('a')[0].contents[0].strip()
            refurl = cols[12].findAll('a')[0]['href'].strip()
            source = get_source(name, reference = reference, url = refurl)
            secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)
            sources = ','.join(list(filter(None, [source, secondarysource])))
            if str(cols[1].contents[0]).strip() != 'unk':
                add_quanta(name, 'claimedtype', str(cols[1].contents[0]).strip(), sources)
            if str(cols[2].contents[0]).strip() != 'anonymous':
                add_quanta(name, 'host', str(cols[2].contents[0]).strip(), sources)
            events[name]['snra'] = str(cols[3].contents[0]).strip()
            events[name]['sndec'] = str(cols[4].contents[0]).strip()
            if str(cols[6].contents[0]).strip() not in ['2440587', '2440587.292']:
                astrot = astrotime(float(str(cols[6].contents[0]).strip()), format='jd')
                events[name]['discoverday'] = str(astrot.datetime.day)
                events[name]['discovermonth'] = str(astrot.datetime.month)
                events[name]['discoveryear'] = str(astrot.datetime.year)
            if str(cols[7].contents[0]).strip() not in ['2440587', '2440587.292']:
                astrot = astrotime(float(str(cols[7].contents[0]).strip()), format='jd')
                if float(str(cols[8].contents[0]).strip()) <= 90.0:
                    add_photometry(name, time = str(astrot.mjd), abmag = str(cols[8].contents[0]).strip(), source = sources)
            if cols[11].contents[0] != 'n/a':
                add_quanta(name, 'redshift', str(cols[11].contents[0]).strip(), sources)
            events[name]['discoverer'] = str(cols[13].contents[0]).strip()

    vsnetfiles = ["latestsne.dat"]
    for vsnetfile in vsnetfiles:
        f = open("../external/" + vsnetfile,'r',encoding='latin1')
        tsvin = csv.reader(f, delimiter=' ', skipinitialspace=True)
        for r, row in enumerate(tsvin):
            if not row or row[0][:4] in ['http', 'www.'] or len(row) < 3:
                continue
            name = row[0].strip()
            if name[:4].isdigit():
                name = 'SN' + name
            if name[:4] == 'PSNJ':
                name = 'PSN J' + name[4:]
            name = add_event(name)
            if not is_number(row[1]):
                continue
            year = row[1][:4]
            month = row[1][4:6]
            day = row[1][6:]
            if '.' not in day:
                day = day[:2] + '.' + day[2:]
            mjd = astrotime(year + '-' + month + '-' + str(floor(float(day))).zfill(2)).mjd + float(day) - floor(float(day))
            abmag = row[2].rstrip(ascii_letters)
            if not is_number(abmag):
                continue
            if abmag.isdigit():
                if int(abmag) > 100:
                    abmag = abmag[:2] + '.' + abmag[2:]
            secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)
            band = row[2].lstrip('1234567890.')
            if len(row) >= 4:
                if is_number(row[3]):
                    aberr = row[3]
                    refind = 4
                else:
                    aberr = ''
                    refind = 3

                if refind >= len(row):
                    sources = secondarysource
                else:
                    reference = ' '.join(row[refind:])
                    source = get_source(name, reference = reference)
                    sources = ','.join([source,secondarysource])
            else:
                sources = secondarysource
            add_photometry(name, time = mjd, band = band, abmag = abmag, aberr = aberr, source = sources)
        f.close()

if dofirstmax:
    for name in events:
        set_first_max_light(name)

if dolennarz:
    Vizier.ROW_LIMIT = -1
    result = Vizier.get_catalogs("J/A+A/538/A120/usc")
    table = result[list(result.keys())[0]]
    table.convert_bytestring_to_unicode(python3_only=True)

    bibcode = "2012A&A...538A.120L"
    for row in table:
        row = convert_aq_output(row)
        name = 'SN' + row['SN']
        name = add_event(name)

        source = get_source(name, bibcode = bibcode)

        if row['Gal']:
            add_quanta(name, 'host', row['Gal'], source)
        if row['z']:
            if name != 'SN1985D':
                add_quanta(name, 'redshift', row['z'], source)
        if row['Dist']:
            add_quanta(name, 'lumdist', row['Dist'], source)

        if row['Ddate']:
            dateparts = row['Ddate'].split('-')
            if len(dateparts) == 3:
                astrot = astrotime(row['Ddate'], scale='utc')
            elif len(dateparts) == 2:
                astrot = astrotime(row['Ddate'] + '-01', scale='utc')
            else:
                astrot = astrotime(row['Ddate'] + '-01-01', scale='utc')

            if 'photometry' not in events[name]:
                if 'Dmag' in row and is_number(row['Dmag']) and not isnan(float(row['Dmag'])):
                    mjd = str(astrot.mjd)
                    add_photometry(name, time = mjd, band = row['Dband'], abmag = row['Dmag'], source = source)
            if 'discoveryear' not in events[name] and 'discovermonth' not in events[name] and 'discoverday' not in events[name]:
                events[name]['discoveryear'] = str(astrot.datetime.year)
                if len(dateparts) >= 2:
                    events[name]['discovermonth'] = str(astrot.datetime.month)
                if len(dateparts) == 3:
                    events[name]['discoverday'] = str(astrot.datetime.day)
        if row['Mdate']:
            dateparts = row['Mdate'].split('-')
            if len(dateparts) == 3:
                astrot = astrotime(row['Mdate'], scale='utc')
            elif len(dateparts) == 2:
                astrot = astrotime(row['Mdate'] + '-01', scale='utc')
            else:
                astrot = astrotime(row['Mdate'] + '-01-01', scale='utc')

            if 'photometry' not in events[name]:
                if 'MMag' in row and is_number(row['MMag']) and not isnan(float(row['MMag'])):
                    mjd = str(astrot.mjd)
                    add_photometry(name, time = mjd, band = row['Mband'], abmag = row['Mmag'], source = source)
            if 'maxyear' not in events[name] and 'maxmonth' not in events[name] and 'maxday' not in events[name]:
                events[name]['maxyear'] = str(astrot.datetime.year)
                if len(dateparts) >= 2:
                    events[name]['maxmonth'] = str(astrot.datetime.month)
                if len(dateparts) == 3:
                    events[name]['maxday'] = str(astrot.datetime.day)
    f.close()

if doogle:
    basenames = ['transients', 'transients/2014b', 'transients/2014', 'transients/2013', 'transients/2012']
    oglenames = []
    for bn in basenames:
        response = urllib.request.urlopen('http://ogle.astrouw.edu.pl/ogle4/' + bn + '/transients.html')
        soup = BeautifulSoup(response.read(), "html5lib")
        links = soup.findAll('a')
        breaks = soup.findAll('br')
        datalinks = []
        for a in links:
            if a.has_attr('href'):
                if '.dat' in a['href']:
                    datalinks.append('http://ogle.astrouw.edu.pl/ogle4/' + bn + '/' + a['href'])

        ec = 0
        reference = 'OGLE-IV Transient Detection System'
        refurl = 'http://ogle.astrouw.edu.pl/ogle4/transients/transients.html'
        for br in breaks:
            sibling = br.nextSibling
            if 'Ra,Dec=' in sibling:
                line = sibling.replace("\n", '').split('Ra,Dec=')
                name = line[0].strip()

                if 'NOVA' in name or 'dupl' in name:
                    continue

                if name in oglenames:
                    continue
                oglenames.append(name)

                name = add_event(name)

                if name[:4] == 'OGLE':
                    if name[4] == '-':
                        if is_number(name[5:9]):
                            events[name]['discoveryear'] = name[5:9]
                    else:
                        if is_number(name[4:6]):
                            events[name]['discoveryear'] = '20' + name[4:6]

                mySibling = sibling.nextSibling
                atelref = ''
                claimedtype = ''
                while 'Ra,Dec=' not in mySibling:
                    if isinstance(mySibling, NavigableString):
                        if 'Phot.class=' in str(mySibling):
                            claimedtype = re.sub(r'\([^)]*\)', '', str(mySibling).split('=')[-1]).replace('SN','').strip()
                    if isinstance(mySibling, Tag):
                        atela = mySibling
                        if atela and atela.has_attr('href') and 'astronomerstelegram' in atela['href']:
                            atelref = a.contents[0].strip()
                            atelurl = a['href']
                    mySibling = mySibling.nextSibling
                    if mySibling is None:
                        break

                nextSibling = sibling.nextSibling
                if isinstance(nextSibling, Tag) and nextSibling.has_attr('alt') and nextSibling.contents[0].strip() != 'NED':
                    radec = nextSibling.contents[0].strip().split()
                else:
                    radec = line[-1].split()
                ra = radec[0]
                dec = radec[1]
                events[name]['snra'] = ra
                events[name]['sndec'] = dec
                lcresponse = urllib.request.urlopen(datalinks[ec])
                lcdat = lcresponse.read().decode('utf-8').splitlines()
                sources = [get_source(name, reference = reference, url = refurl)]
                if atelref and atelref != 'ATel#----':
                    sources.append(get_source(name, reference = atelref, url = atelurl))
                sources = ','.join(sources)
                if claimedtype and claimedtype != '-':
                    add_quanta(name, 'claimedtype', claimedtype, sources)
                elif 'SN' not in name and 'claimedtype' not in events[name]:
                    add_quanta(name, 'claimedtype', 'Candidate', sources)
                for row in lcdat:
                    row = row.split()
                    mjd = str(jd_to_mjd(Decimal(row[0])))
                    abmag = row[1]
                    if float(abmag) > 90.0:
                        continue
                    aberr = row[2]
                    upperlimit = False
                    if aberr == '-1' or float(aberr) > 10.0:
                        aberr = ''
                        upperlimit = True
                    add_photometry(name, time = mjd, band = 'I', abmag = abmag, aberr = aberr, source = sources, upperlimit = upperlimit)
                ec += 1

if donedd:
    f = open("../external/NED25.12.1-D-10.4.0-20151123.csv", 'r')
    data = csv.reader(f, delimiter=',', quotechar='"')
    reference = "NED-D"
    refurl = "http://ned.ipac.caltech.edu/Library/Distances/"
    oldhostname = ''
    for r, row in enumerate(data):
        if r <= 12:
            continue
        hostname = row[3]
        #if hostname == oldhostname:
        #    continue
        distmod = row[4]
        moderr = row[5]
        dist = row[6]
        disterr = ''
        if moderr:
            sig = get_sig_digits(moderr)
            disterr = pretty_num(1.0e-6*(10.0**(0.2*(5.0 + float(distmod))) * (10.0**(0.2*float(moderr)) - 1.0)), sig = sig)
        bibcode = row[8]
        name = ''
        if hostname[:3] == 'SN ':
            if is_number(hostname[3:7]):
                name = 'SN' + hostname[3:]
            else:
                name = hostname[3:]
        if hostname[:5] == 'SNLS ':
            name = 'SNLS-' + hostname[5:].split()[0]
        if name:
            name = add_event(name)
            secondarysource = get_source(name, reference = reference, url = refurl, secondary = True)
            if bibcode:
                source = get_source(name, bibcode = bibcode)
                sources = ','.join([source, secondarysource])
            else:
                sources = secondarysource
            add_quanta(name, 'lumdist', dist, sources, error = disterr)
        #else:
        #    cleanhost = hostname.replace('MESSIER 0', 'M').replace('MESSIER ', 'M').strip()
        #    for name in events:
        #        if 'host' in events[name]:
        #            for host in events[name]['host']:
        #                if host['value'] == cleanhost:
        #                    print ('found host: ' + cleanhost)
        #                    secondarysource = get_source(name, reference = reference, url = refurl, secondary = True)
        #                    if bibcode:
        #                        source = get_source(name, bibcode = bibcode)
        #                        sources = ','.join([source, secondarysource])
        #                    else:
        #                        sources = secondarysource
        #                    add_quanta(name, 'lumdist', dist, sources, error = disterr)
        #                    break
        oldhostname = hostname

if docfaiaspectra:
    for name in sorted(next(os.walk("../sne-external-spectra/CfA_SNIa"))[1], key=lambda s: s.lower()):
        fullpath = "../sne-external-spectra/CfA_SNIa/" + name
        if name[:2] == 'sn' and is_number(name[2:6]):
            name = 'SN' + name[2:]
        name = add_event(name)
        reference = 'CfA Supernova Archive'
        refurl = 'https://www.cfa.harvard.edu/supernova/SNarchive.html'
        source = get_source(name, reference = reference, url = refurl, secondary = True)
        for file in sorted(glob.glob(fullpath + '/*'), key=lambda s: s.lower()):
            fileparts = os.path.basename(file).split('-')
            if name[:2] == "SN":
                year = fileparts[1][:4]
                month = fileparts[1][4:6]
                day = fileparts[1][6:]
                instrument = fileparts[2].split('.')[0]
            else:
                year = fileparts[2][:4]
                month = fileparts[2][4:6]
                day = fileparts[2][6:]
                instrument = fileparts[3].split('.')[0]
            time = astrotime(year + '-' + month + '-' + str(floor(float(day))).zfill(2)).mjd + float(day) - floor(float(day))
            f = open(file,'r')
            data = csv.reader(f, delimiter=' ', skipinitialspace=True)
            data = [list(i) for i in zip(*data)]
            wavelengths = data[0]
            fluxes = data[1]
            errors = data[2]
            add_spectrum(name = name, waveunit = 'Angstrom', fluxunit = 'erg/s/cm^2/Angstrom',
                wavelengths = wavelengths, fluxes = fluxes, timeunit = 'MJD', time = time, instrument = instrument,
                errorunit = "ergs/s/cm^2/Angstrom", errors = errors, source = source, dereddened = False, deredshifted = False)

if docfaibcspectra:
    for name in sorted(next(os.walk("../sne-external-spectra/CfA_SNIbc"))[1], key=lambda s: s.lower()):
        fullpath = "../sne-external-spectra/CfA_SNIbc/" + name
        if name[:2] == 'sn' and is_number(name[2:6]):
            name = 'SN' + name[2:]
        name = add_event(name)
        reference = 'CfA Supernova Archive'
        refurl = 'https://www.cfa.harvard.edu/supernova/SNarchive.html'
        source = get_source(name, reference = reference, url = refurl, secondary = True)
        for file in sorted(glob.glob(fullpath + '/*'), key=lambda s: s.lower()):
            fileparts = os.path.basename(file).split('-')
            instrument = ''
            year = fileparts[1][:4]
            month = fileparts[1][4:6]
            day = fileparts[1][6:].split('.')[0]
            if len(fileparts) > 2:
                instrument = fileparts[-1].split('.')[0]
            time = astrotime(year + '-' + month + '-' + str(floor(float(day))).zfill(2)).mjd + float(day) - floor(float(day))
            f = open(file,'r')
            data = csv.reader(f, delimiter=' ', skipinitialspace=True)
            data = [list(i) for i in zip(*data)]
            wavelengths = data[0]
            fluxes = data[1]
            add_spectrum(name = name, waveunit = 'Angstrom', fluxunit = 'Uncalibrated', wavelengths = wavelengths,
                fluxes = fluxes, timeunit = 'MJD', time = time, instrument = instrument, source = source,
                dereddened = False, deredshifted = False)

if dosnlsspectra:
    for file in sorted(glob.glob('../sne-external-spectra/SNLS/*'), key=lambda s: s.lower()):
        fileparts = os.path.basename(file).split('_')
        name = 'SNLS-' + fileparts[1]
        name = add_event(name)
        events[name]['discoveryear'] = '20' + fileparts[1][:2]

        source = get_source(name, bibcode = "2009A&A...507...85B")

        f = open(file,'r')
        data = csv.reader(f, delimiter=' ', skipinitialspace=True)
        specdata = []
        for r, row in enumerate(data):
            if row[0] == '@TELESCOPE':
                instrument = row[1].strip()
            elif row[0] == '@REDSHIFT':
                add_quanta(name, 'redshift', row[1].strip(), source)
            if r < 14:
                continue
            specdata.append(list(filter(None, [x.strip(' \t') for x in row])))
        specdata = [list(i) for i in zip(*specdata)]
        wavelengths = specdata[1]
        
        fluxes = [pretty_num(float(x)*1.e-16, sig = get_sig_digits(x)) for x in specdata[2]]
        errors = [pretty_num(float(x)*1.e-16, sig = get_sig_digits(x)) for x in specdata[3]]

        add_spectrum(name = name, waveunit = 'Angstrom', fluxunit = 'erg/s/cm^2/Angstrom', wavelengths = wavelengths,
            fluxes = fluxes, instrument = instrument, source = source)

if docspspectra:
    for file in sorted(glob.glob('../sne-external-spectra/CSP/*'), key=lambda s: s.lower()):
        sfile = os.path.basename(file).split('.')
        if sfile[1] == 'txt':
            continue
        sfile = sfile[0]
        fileparts = sfile.split('_')
        name = 'SN20' + fileparts[0][2:]
        name = add_event(name)
        instrument = ': '.join(fileparts[-2:])
        source = get_source(name, bibcode = "2013ApJ...773...53F")

        f = open(file,'r')
        data = csv.reader(f, delimiter=' ', skipinitialspace=True)
        specdata = []
        for r, row in enumerate(data):
            if row[0] == '#JDate_of_observation:':
                jd = row[1].strip()
                time = str(jd_to_mjd(Decimal(jd)))
            elif row[0] == '#Redshift:':
                add_quanta(name, 'redshift', row[1].strip(), source)
            if r < 7:
                continue
            specdata.append(list(filter(None, [x.strip(' ') for x in row])))
        specdata = [list(i) for i in zip(*specdata)]
        wavelengths = specdata[0]
        fluxes = specdata[1]

        add_spectrum(name = name, timeunit = 'MJD', time = time, waveunit = 'Angstrom', fluxunit = 'erg/s/cm^2/Angstrom', wavelengths = wavelengths,
            fluxes = fluxes, instrument = instrument, source = source, deredshifted = True)

if doucbspectra:
    secondaryreference = "UCB Filippenko Group's Supernova Database (SNDB)"
    secondaryrefurl = "http://heracles.astro.berkeley.edu/sndb/info"

    path = os.path.abspath('../sne-external-spectra/UCB/sndb.html')
    response = urllib.request.urlopen('file://' + path)

    soup = BeautifulSoup(response.read(), "html5lib")
    i = 0
    for t, tr in enumerate(soup.findAll('tr')):
        if t == 0:
            continue
        for d, td in enumerate(tr.findAll('td')):
            if d == 2:
                claimedtype = td.contents[0].strip()
            elif d == 4:
                filename = td.contents[0].strip()
                name = filename.split('-')[0]
                if name[:2].upper() == 'SN':
                    name = name[:2].upper() + name[2:]
                    if len(name) == 7:
                        name = name[:6] + name[6].upper()
            elif d == 5:
                epoch = td.contents[0].strip()
                year = epoch[:4]
                month = epoch[4:6]
                day = epoch[6:]
                sig = get_sig_digits(day) + 5
                mjd = pretty_num(astrotime(year + '-' + month + '-' + str(floor(float(day))).zfill(2)).mjd + float(day) - floor(float(day)), sig = sig)
            elif d == 7:
                instrument = '' if td.contents[0].strip() == 'None' else td.contents[0].strip()
            elif d == 9:
                snr = td.contents[0].strip()
            elif d == 10:
                observerreducer = td.contents[0].strip().split('|')
                observer = '' if observerreducer[0].strip() == 'None' else observerreducer[0].strip()
                reducer = '' if observerreducer[1].strip() == 'None' else observerreducer[1].strip()
            elif d == 11:
                bibcode = td.findAll('a')[0].contents[0]

        name = add_event(name)
        source = get_source(name, bibcode = bibcode)
        secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)
        sources = ','.join([source, secondarysource])
        add_quanta(name, 'claimedtype', claimedtype, sources)

        with open('../sne-external-spectra/UCB/' + filename) as f:
            specdata = list(csv.reader(f, delimiter=' ', skipinitialspace=True))
            startrow = 0
            for row in specdata:
                if row[0][0] == '#':
                    startrow += 1
                else:
                    break
            specdata = specdata[startrow:]

            haserrors = len(specdata[0]) == 3 and specdata[0][2] and specdata[0][2] != 'NaN'
            specdata = [list(i) for i in zip(*specdata)]

            wavelengths = specdata[0]
            fluxes = specdata[1]
            errors = ''
            if haserrors:
                errors = specdata[2]

            if not list(filter(None, errors)):
                errors = ''

            add_spectrum(name = name, timeunit = 'MJD', time = mjd, waveunit = 'Angstrom', fluxunit = 'Uncalibrated', wavelengths = wavelengths,
                fluxes = fluxes, errors = errors, errorunit = 'Uncalibrated', instrument = instrument, source = source, snr = snr, observer = observer, reducer = reducer,
                deredshifted = True)

if dosuspectspectra:
    folders = next(os.walk('../sne-external-spectra/SUSPECT'))[1]
    for folder in folders:
        print('../sne-external-spectra/SUSPECT/'+folder)
        eventfolders = next(os.walk('../sne-external-spectra/SUSPECT/'+folder))[1]
        for eventfolder in eventfolders:
            name = eventfolder
            if is_number(name[:4]):
                name = 'SN' + name
            name = add_event(name)
            secondaryreference = "SUSPECT"
            secondaryrefurl = "https://www.nhn.ou.edu/~suspect/"
            secondarysource = get_source(name, reference = secondaryreference, url = secondaryrefurl, secondary = True)
            eventspectra = next(os.walk('../sne-external-spectra/SUSPECT/'+folder+'/'+eventfolder))[2]
            for spectrum in eventspectra:
                date = spectrum.split('_')[1]
                year = date[:4]
                month = date[4:6]
                day = date[6:]
                sig = get_sig_digits(day) + 5
                time = pretty_num(astrotime(year + '-' + month + '-' + str(floor(float(day))).zfill(2)).mjd + float(day) - floor(float(day)), sig = sig)

                with open('../sne-external-spectra/SUSPECT/'+folder+'/'+eventfolder+'/'+spectrum) as f:
                    specdata = list(csv.reader(f, delimiter=' ', skipinitialspace=True))
                    specdata = list(filter(None, specdata))
                haserrors = len(specdata[0]) == 3 and specdata[0][2] and specdata[0][2] != 'NaN'
                specdata = [list(i) for i in zip(*specdata)]

                wavelengths = specdata[0]
                fluxes = specdata[1]
                errors = ''
                if haserrors:
                    errors = specdata[2]

                add_spectrum(name = name, timeunit = 'MJD', time = time, waveunit = 'Angstrom', fluxunit = 'Uncalibrated', wavelengths = wavelengths,
                    fluxes = fluxes, errors = errors, errorunit = 'Uncalibrated', source = secondarysource)

if writeevents:
    # Calculate some columns based on imported data, sanitize some fields
    for name in events:
        if 'claimedtype' in events[name]:
            events[name]['claimedtype'][:] = [ct for ct in events[name]['claimedtype'] if (ct['value'] != '?' and ct['value'] != '-')]
        if 'redshift' in events[name] and 'hvel' not in events[name]:
            # Find the "best" redshift to use for this
            bestsig = 0
            for z in events[name]['redshift']:
                sig = get_sig_digits(z['value'])
                if sig > bestsig:
                    bestz = z['value']
                    bestsig = sig
            if bestsig > 0:
                bestz = float(bestz)
                add_quanta(name, 'hvel', pretty_num(clight/1.e5*((bestz + 1.)**2. - 1.)/
                    ((bestz + 1.)**2. + 1.), sig = bestsig), 'D')
        elif 'hvel' in events[name] and 'redshift' not in events[name]:
            # Find the "best" hvel to use for this
            bestsig = 0
            for hv in events[name]['hvel']:
                sig = get_sig_digits(hv['value'])
                if sig > bestsig:
                    besthv = hv['value']
                    bestsig = sig
            if bestsig > 0 and is_number(besthv):
                voc = float(besthv)*1.e5/clight
                add_quanta(name, 'redshift', pretty_num(sqrt((1. + voc)/(1. - voc)) - 1., sig = bestsig), 'D')
        if 'maxabsmag' not in events[name] and 'maxappmag' in events[name] and 'lumdist' in events[name]:
            # Find the "best" distance to use for this
            bestsig = 0
            for ld in events[name]['lumdist']:
                sig = get_sig_digits(ld['value'])
                if sig > bestsig:
                    bestld = ld['value']
                    bestsig = sig
            if bestsig > 0 and is_number(bestld) and float(bestld) > 0.:
                events[name]['maxabsmag'] = pretty_num(float(events[name]['maxappmag']) - 5.0*(log10(float(bestld)*1.0e6) - 1.0), sig = bestsig)
        if 'redshift' in events[name]:
            # Find the "best" redshift to use for this
            bestsig = 0
            for z in events[name]['redshift']:
                sig = get_sig_digits(z['value'])
                if sig > bestsig:
                    bestz = z['value']
                    bestsig = sig
            if bestsig > 0 and float(bestz) > 0.:
                if 'lumdist' not in events[name]:
                    dl = cosmo.luminosity_distance(float(bestz))
                    add_quanta(name, 'lumdist', pretty_num(dl.value, sig = bestsig), 'D')
                    if 'maxabsmag' not in events[name] and 'maxappmag' in events[name]:
                        events[name]['maxabsmag'] = pretty_num(float(events[name]['maxappmag']) - 5.0*(log10(dl.to('pc').value) - 1.0), sig = bestsig)
        if 'photometry' in events[name]:
            events[name]['photometry'].sort(key=lambda x: (float(x['time']), x['band'], float(x['abmag'])))
        if 'spectra' in events[name] and list(filter(None, ['time' in x for x in events[name]['spectra']])):
            events[name]['spectra'].sort(key=lambda x: float(x['time']))
        events[name] = OrderedDict(sorted(events[name].items(), key=lambda key: event_attr_priority(key[0])))

    # Delete all old event JSON files
    for folder in repfolders:
        filelist = glob.glob("../" + folder + "/*.json")
        for f in filelist:
            os.remove(f)

    # Write it all out!
    for name in events:
        print('Writing ' + name)
        filename = event_filename(name)

        jsonstring = json.dumps({name:events[name]}, indent=4, separators=(',', ': '), ensure_ascii=False)

        outdir = '../'
        if 'discoveryear' in events[name]:
            for r, year in enumerate(repyears):
                if int(events[name]['discoveryear']) <= year:
                    outdir += repfolders[r]
                    break
        else:
            outdir += str(repfolders[0])

        f = codecs.open(outdir + '/' + filename + '.json', 'w', encoding='utf8')
        f.write(jsonstring)
        f.close()

print("Memory used (MBs on Mac, GBs on Linux): " + "{:,}".format(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024./1024.))

# Print some useful facts
if printextra:
    print('Printing events without any photometry:')
    for name in events:
        if 'photometry' not in events[name]:
            print(name)

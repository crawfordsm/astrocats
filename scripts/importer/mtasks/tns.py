"""General data import tasks.
"""
import csv
import os
from math import ceil

import requests
from astropy.time import Time as astrotime

from scripts import PATH
from scripts.utils import pbar, pretty_num

from .. import Events
from ..funcs import add_photometry, load_cached_url


def do_tns(catalog):
    from datetime import timedelta
    session = requests.Session()
    current_task = task_obj.current_task(args)
    tns_url = 'https://wis-tns.weizmann.ac.il/'
    search_url = tns_url + \
        'search?&num_page=1&format=html&sort=desc&order=id&format=csv&page=0'
    csvtxt = load_cached_url(args, current_task, search_url, os.path.join(
        PATH.REPO_EXTERNAL, 'TNS/index.csv'))
    if not csvtxt:
        return events
    maxid = csvtxt.splitlines()[1].split(',')[0].strip('"')
    maxpages = ceil(int(maxid) / 1000.)

    for page in pbar(range(maxpages), current_task):
        fname = os.path.join(PATH.REPO_EXTERNAL, 'TNS/page-') + \
            str(page).zfill(2) + '.csv'
        if task_obj.load_archive(args) and os.path.isfile(fname) and page < 7:
            with open(fname, 'r') as tns_file:
                csvtxt = tns_file.read()
        else:
            with open(fname, 'w') as tns_file:
                session = requests.Session()
                ses_url = (tns_url + 'search?&num_page=1000&format=html&edit'
                           '[type]=&edit[objname]=&edit[id]=&sort=asc&order=id'
                           '&display[redshift]=1'
                           '&display[hostname]=1&display[host_redshift]=1'
                           '&display[source_group_name]=1'
                           '&display[programs_name]=1'
                           '&display[internal_name]=1&display[isTNS_AT]=1'
                           '&display[public]=1'
                           '&display[end_pop_period]=0'
                           '&display[spectra_count]=1'
                           '&display[discoverymag]=1&display[discmagfilter]=1'
                           '&display[discoverydate]=1&display[discoverer]=1'
                           '&display[sources]=1'
                           '&display[bibcode]=1&format=csv&page=' + str(page))
                response = session.get(ses_url)
                csvtxt = response.text
                tns_file.write(csvtxt)

        tsvin = list(csv.reader(csvtxt.splitlines(), delimiter=','))
        for ri, row in enumerate(pbar(tsvin, current_task, leave=False)):
            if ri == 0:
                continue
            if row[4] and 'SN' not in row[4]:
                continue
            name = row[1].replace(' ', '')
            name = catalog.add_event(name)
            source = catalog.events[name].add_source(
                srcname='Transient Name Server', url=tns_url)
            catalog.events[name].add_quantity('alias', name, source)
            if row[2] and row[2] != '00:00:00.00':
                catalog.events[name].add_quantity('ra', row[2], source)
            if row[3] and row[3] != '+00:00:00.00':
                catalog.events[name].add_quantity('dec', row[3], source)
            if row[4]:
                catalog.events[name].add_quantity(
                    'claimedtype', row[4].replace('SN', '').strip(), source)
            if row[5]:
                catalog.events[name].add_quantity(
                    'redshift', row[5], source, kind='spectroscopic')
            if row[6]:
                catalog.events[name].add_quantity('host', row[6], source)
            if row[7]:
                catalog.events[name].add_quantity(
                    'redshift', row[7], source, kind='host')
            if row[8]:
                catalog.events[name].add_quantity('discoverer', row[8], source)
            # Currently, all events listing all possible observers. TNS bug?
            # if row[9]:
            #    observers = row[9].split(',')
            #    for observer in observers:
            #        catalog.events[name].add_quantity('observer', observer.strip(),
            #                                  source)
            if row[10]:
                catalog.events[name].add_quantity('alias', row[10], source)
            if row[8] and row[14] and row[15] and row[16]:
                survey = row[8]
                magnitude = row[14]
                band = row[15].split('-')[0]
                mjd = astrotime(row[16]).mjd
                add_photometry(events, name, time=mjd, magnitude=magnitude,
                               band=band,
                               survey=survey, source=source)
            if row[16]:
                date = row[16].split()[0].replace('-', '/')
                if date != '0000/00/00':
                    date = date.replace('/00', '')
                    time = row[16].split()[1]
                    if time != '00:00:00':
                        ts = time.split(':')
                        dt = timedelta(hours=int(ts[0]), minutes=int(
                            ts[1]), seconds=int(ts[2]))
                        date += pretty_num(dt.total_seconds() /
                                           (24 * 60 * 60), sig=6).lstrip('0')
                    catalog.events[name].add_quantity('discoverdate', date, source)
            if args.update:
                events = Events.journal_events(
                    tasks, args, events, log)

    catalog.journal_events()
    return events

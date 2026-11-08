#!/usr/bin/env python3
"""Snapshot, review and clear an explicitly selected RSS backlog.

The ranking assists a manual review; it is not a scheduled curation policy.
Apply marks only snapshot items read, using the reader's existing batch action.
"""
import argparse
import collections
import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SOURCE_WEIGHT = {
    'David Lebovitz': 32, 'xkcd.com': 32, 'Hugging Face - Blog': 12,
    'The GitHub Blog': 12, 'NVIDIA Technical Blog': 7,
    'NYT > Science': 10, 'Science | The Guardian': 8,
    'NYT > Food': 10, 'Travel | The Guardian': 12, 'NYT > Travel': 6,
    'Nature': 5, 'NYT > Arts': 5, 'NYT > The New York Times Magazine': 2,
    'www.theregister.com - Articles': 2, "Latest from Tom's Hardware": -6,
    'NYT > Top Stories': -7, 'NYT > Technology': 5,
    'Google Developers Blog': 10, 'Google Open Source Blog': 12,
    'Cloud Blog': -7, 'The Official Google Blog': -5, 'AWS News Blog': 6,
    'Figma product news and release notes': 2, 'marketsforISK': -8,
}
SOURCE_CAP = {'NYT > Arts': 140, 'NYT > Science': 120, 'Nature': 150,
              'Cloud Blog': 45, 'NVIDIA Technical Blog': 60,
              'Hugging Face - Blog': 60, 'Science | The Guardian': 130}

# Corrections from review of the backlog's titles and summaries.
REVIEW_EXCLUSIONS = [
    r'^(Author Correction:|Publisher Correction:|Addendum:|Retraction Note:)',
    r'\b(epigenome|immune multiome|bladder cancer subtypes|fucoidans|RNA transfer vehicles|lipids are organized|electrical double layers|glycolipids|alkalinity export|atmospheric CH|eQTLs)\b',
    r'\b(DYNLL1|MRE11|GRP75|SIMPA|single.cell atlas|stereotaxic|inter.organellar)\b',
    r'\b(Dolly Parton.s (nephew|death set off|marriage|cautious politics)|Dolly Parton and the|Dolly Parton Was So Fake|Dolly Parton Died After|Dolly Parton.s Best Movie|Dolly Parton.s Other Legacy|The South Loved Dolly|airport for Dolly|King Charles III|Princess Diana)\b',
    r'\b(LL Cool J|Cardi B|Olivia Rodrigo|Harry Styles|werewolf romance|Chuck Lorre|Ari Emanuel|book parties|book group names new|best movies and TV|seven more shows|more theater to stream|microdramas|cringey millennial)\b',
    r'\b(3D.printed (firearm|machine gun|rocket|.*bomb)|GatGPT|Glock switches|Jensen Huang.signed|iPhone 18 Pro|La Croix|Chinese.*written roadmap|Hoto.s cordless|shocking.*price hike)\b',
    r'\b(autonomous combat drone|Houthi rebels|hypersonic missiles|air.defense suppression tools|U\.S\. Army developing program)\b',
    r'\b(Fed Governor (Lisa )?Cook|Antifa Terrorists|NYC Restaurants and Trends|Restaurants That Came in From the Cold|Restaurant Theft|Busiest Uber Eats|Lili Reinhart|Madelyn Cline|Outer Banks|Bar CC|Cleo in New York)\b',
    r'\b(How going dual.native|Closing the observability gap for the AI.ready enterprise|Mars astronauts could live in houses)\b',
    r'^(Help!|Misery at Heathrow|Amazon Suspends Flights|The T\.S\.A\.|Travelers Face More Flight Delays|5 New Hotels for Your Late Summer|Cold Plunge Tips|Southwest to Open Premium Airport|What to Know About the Grand Canyon Flooding)',
]

# Title matches carry their full weight; a summary-only match carries half.
THEMES = [
    ('particle physics', 25, r'\b(CERN|ATLAS experiment|(?-i:EIC|ePIC|PanDA|RHIC)|Brookhaven|Higgs|collider|particle physics|neutrino|antimatter|dark matter|quantum gravity|standard model|nuclear physics)\b'),
    ('AI systems and coding', 18, r'\b(AI agents?|A\.I\. agents?|coding agents?|software agents?|agentic|persistent memory|learned memory|context window|context engineering|MCP|Claude|Codex|Opus|LLM|language models?|open.weights?|local AI|Ollama|RAG|retrieval|inference|AI coding|AI assistant|agent skills)\b'),
    ('graphics and virtual worlds', 23, r'\b(Blender|WebGPU|WebGL|Three\.?js|glTF|GLB|avatars?|animation|motion capture|rendering|ray tracing|3D|procedural|photogrammetry|geospatial|terrain|volumetric|digital twins?|virtual worlds?|shaders?)\b'),
    ('software and computing', 15, r'\b(Python|Rust|PostgreSQL|Postgres|Django|SQLite|Linux|open source|distributed computing|Slurm|HTCondor|Kubernetes|Docker|containers?|GitHub Actions|workflow|debugging|profiling|observability|performance|API|SSH|backups?|permissions?|supply.chain|vulnerabilit\w*|ransomware)\b'),
    ('astronomy and fundamental science', 19, r'\b(astronomy|cosmology|galax\w*|universe|black holes?|JWST|James Webb|Voyager|exoplanets?|solar system|gravitational waves?|quantum|physics|mathematics|mathematicians?|telescope|relativity|supernova|dark energy)\b'),
    ('Earth and natural history', 13, r'\b(fossils?|dinosaurs?|T rex|North Dakota|Montana|Yellowstone|Williston|Badlands|geology|volcano|glaciers?|archaeolog\w*|evolution|biodiversity|forests?|wildlife|birds?|oceans?|rivers?|climate science)\b'),
    ('food and cooking', 13, r'\b(recipes?|cook\w*|baking|bread|beans|stew|soup|chocolate|pasta|cheese|curry|tagine|miso|olive oil|wine|culinary|restaurant|cuisine)\b'),
    ('travel and outdoors', 12, r'\b(hik\w*|camping|walks?|walking|cycling|canoe|sailing|national parks?|road trips?|rail journeys?|train journeys?|architecture|Frank Lloyd Wright|Goldsworthy|Tolkien|Geneva|Regina|Saskatchewan|North Dakota|Montana)\b'),
    ('arts and music', 17, r'\b(poetry|poems?|Gary Snyder|Steinbeck|Degas|Goldsworthy|Lucinda Williams|Dolly Parton|Willie Nelson|Bob Dylan|Leonard Cohen|Emmylou|outlaw country|country music|Americana|folk music|Tolkien|Toni Morrison|Cormac McCarthy|John Prine|Joni Mitchell|Neil Young|Springsteen|Tom Waits|Van Morrison|Annie Dillard|Mary Oliver|Wendell Berry|Seamus Heaney|Rilke|Patti Smith)\b'),
    ('cultural craft', 11, r'\b(museums?|sculpt\w*|paintings?|exhibition|jazz|guitar|singer|songwriter|songs?|music|novelist|fiction|film|cinema|theater|theatre|architect\w*|ceramic\w*)\b'),
    ('substantial reading', 8, r'\b(essay|oral history|obituary|obituaries|retrospective|biography|biographies|memoir|books?|novels?|literature|craft|attention|consciousness|scientific discovery|science funding|research funding|scientific research)\b'),
]
PENALTIES = [
    ('shopping promotion', 1000, r'\b(deals?|discount|coupon|prime day|black friday|savings?|save \$|save \d+%|\d+% off|off at amazon|lowest price|all.time.low|price drops?|on sale|buy now|sales event|giveaway|could be yours for|now just \$)'),
    ('consumer product review', 23, r'\b(review|unboxing|best gaming|gaming headset|gaming chair|power supply|mechanical keyboard|RGB|gaming monitor)\b'),
    ('marketing or event promotion', 20, r'\b(webinar|sponsored|customer story|success story|forrester|gartner|magic quadrant|keynote|badge.a.thon|enterprise transformation|business leaders|businesses|marketers|marketing|advertis\w*|brand strategy|register now|join us|recap)\b'),
    ('short lived news', 16, r'\b(live updates|live news|what to know today|today.s wordle|connections hints|crossword|horoscope|daily briefing|morning briefing|evening briefing|weather forecast|election live|latest poll|stock market today|earnings|stock price|box office|fashion week)\b'),
    ('dated viewing notice or solicitation', 14, r'\b(watch.{0,20}launch|set to launch|starwatch|meteor shower|eclipse.{0,30}(tonight|thursday)|tell us|we want to (hear|answer)|do you have questions|sign up|trailer)\b'),
    ('routine entertainment coverage', 17, r'\b(late night|late-night|shows? to watch|TV this week|TV to watch|No\. 1|week at no|more songs we|music awards|film festival|goes viral|going viral|celebrity|red carpet|People.s Choice|best and worst moments)\b'),
    ('travel disruption or advice column', 14, r'\b(Help!|stranded|flight delays?|air traffic disruption|TSA|T\.S\.A\.|unruly passenger|head shots|checkpoints|traveling while pregnant|traveling during labor day|rabies|restrained with duct tape|what to know|passport applications|weekend in miami)\b'),
    ('sport and celebrity', 23, r'\b(NFL|NBA|MLB|NHL|football|baseball|basketball|soccer|premier league|world series|celebrity|red carpet|royal family|Taylor Swift|Kardashian)\b'),
    ('specialist biomedicine', 35, r'\b(single.cell|proteomic\w*|transcriptom\w*|genome.wide|genomic|methylation|chromatin|T.cells?|B.cells?|receptor|tumour|tumor|oncolog\w*|carcinoma|cytokine|mitochondri\w*|organoid|glycosyl\w*|kinase|epigen\w*|multiome|lipidomic|glial|insula|vaccinology|eQTLs|forebrain|prefrontal cortex)\b'),
]


def plain(text):
    return html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))


def score(row):
    value = SOURCE_WEIGHT.get(row['source'], 0)
    title = plain(row['title'])
    summary = plain(row.get('precis'))
    reasons = []
    if any(re.search(pattern, title, re.I) for pattern in REVIEW_EXCLUSIONS):
        return -10000, ['excluded during review']
    for name, weight, pattern in THEMES:
        if re.search(pattern, title, re.I):
            value += weight
            reasons.append(name)
        elif re.search(pattern, summary, re.I):
            value += weight / 2
            reasons.append(name + ' (summary)')
    for name, weight, pattern in PENALTIES:
        if re.search(pattern.removesuffix(r'\b'), title, re.I):
            value -= weight
            reasons.append('-' + name)
    return value, reasons


def url_key(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.removeprefix('www.'), p.path.rstrip('/'), '', ''))


def snapshot(path):
    import bootstrap  # noqa: F401
    from django.db import connection, transaction
    from tjai_app.models import RssItem
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        rows = list(RssItem.objects.filter(read=False).order_by('guid').values())
    result = {'captured': datetime.now(timezone.utc).isoformat(), 'items': rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(result, f, default=str, ensure_ascii=False, indent=2)
    print(json.dumps({'snapshot': str(path), 'items': len(rows)}))


def rank(path, plan_path):
    raw = path.read_bytes()
    rows = json.loads(raw)['items']
    for row in rows:
        row['score'], row['reasons'] = score(row)
    rows.sort(key=lambda r: (r['score'], str(r['published']), r['guid']), reverse=True)
    target = math.ceil(len(rows) * .2)
    seen = set()
    selected = []
    source_counts = collections.Counter()
    for row in rows:
        key = url_key(row['url'])
        if key in seen:
            row['reasons'].append('-duplicate URL')
            continue
        if len(selected) < target and source_counts[row['source']] < SOURCE_CAP.get(row['source'], target):
            selected.append(row['guid'])
            source_counts[row['source']] += 1
            seen.add(key)
    plan = {'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
            'captured': json.loads(raw)['captured'], 'snapshot_count': len(rows),
            'keep': selected, 'reviewed': False, 'ranked': rows}
    with plan_path.open('x') as f:
        json.dump(plan, f, default=str, ensure_ascii=False, indent=2)
    summary(plan)


def summary(plan):
    keep = set(plan['keep'])
    counts = collections.Counter(r['source'] for r in plan['ranked'] if r['guid'] in keep)
    print(json.dumps({'snapshot_count': plan['snapshot_count'], 'keep': len(keep),
                      'clear': plan['snapshot_count'] - len(keep), 'sources': counts}, indent=2))


def finish_review(plan_path):
    plan = json.loads(plan_path.read_text())
    plan['reviewed'] = True
    plan['reviewed_at'] = datetime.now(timezone.utc).isoformat()
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    summary(plan)


def apply(path, plan_path):
    import bootstrap  # noqa: F401
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.test import RequestFactory
    from tjai_app.models import RssItem
    from tjai_app.views import api_rss_mark_guids_read
    raw = path.read_bytes()
    plan = json.loads(plan_path.read_text())
    if hashlib.sha256(raw).hexdigest() != plan['snapshot_sha256'] or not plan['reviewed']:
        raise ValueError('A reviewed plan matching the snapshot is required')
    original = {r['guid'] for r in json.loads(raw)['items']}
    keep = set(plan['keep'])
    if not keep or not keep <= original or len(keep) != len(plan['keep']):
        raise ValueError('Invalid keep selection')
    clear = sorted(original - keep)
    user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    if user is None:
        raise ValueError('No active local administrative user')
    request = RequestFactory().post('/tjai/api/rss/mark-guids-read',
                                   data=json.dumps({'guids': clear}), content_type='application/json')
    request.user = user
    with transaction.atomic():
        response = api_rss_mark_guids_read(request)
        result = json.loads(response.content)
        if response.status_code != 200 or not result.get('ok'):
            raise RuntimeError(result)
        if RssItem.objects.filter(guid__in=clear, read=False).exists():
            raise RuntimeError('Some cleared items remain unread')
        result['kept_unread'] = RssItem.objects.filter(guid__in=keep, read=False).count()
    receipt = plan_path.with_suffix('.receipt.json')
    receipt.write_text(json.dumps({'applied': datetime.now(timezone.utc).isoformat(),
                                   'snapshot_sha256': plan['snapshot_sha256'],
                                   'cleared_guids': clear, **result}, indent=2))
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['snapshot', 'rank', 'summary', 'finish-review', 'apply'])
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.action == 'snapshot':
        snapshot(args.snapshot)
    elif not args.plan:
        parser.error('--plan is required')
    elif args.action == 'rank':
        rank(args.snapshot, args.plan)
    elif args.action == 'summary':
        summary(json.loads(args.plan.read_text()))
    elif args.action == 'finish-review':
        finish_review(args.plan)
    else:
        apply(args.snapshot, args.plan)


if __name__ == '__main__':
    main()

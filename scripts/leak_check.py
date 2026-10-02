#!/usr/bin/env python3
"""leak_check: look for personal data before publishing the repository.

usage: python3 scripts/leak_check.py [--history] [--terms-file FILE]

Public patterns (always on, also in CI): home folders other than the placeholders /Users/you and /home/you, e-mail
addresses (except example domains and GitHub noreply addresses), and token formats (GitHub, sk- style API keys, AWS,
Slack, Google API keys, private key headers).

--terms-file FILE: a private list kept OUTSIDE the repository (it may name clients or people), one entry per line:
    <text>          a term, matched as a case-insensitive substring
    re: <regex>     a Python regular expression, case-insensitive
    allow: <text>   exact text that is allowed even though it contains a term (removed from the line before matching)
    # comment
--history: also scan every commit reachable from any ref (message and patch) and every author/committer identity.

Exit: 0 = nothing found; 1 = findings, printed as <where>:<line>: [<rule>] <excerpt>; 2 = usage error.
"""
import argparse
import re
import subprocess
import sys

PUBLIC = [
    ('home-path', re.compile(r'/Users/(?!you\b)[A-Za-z0-9._-]+')),
    ('home-path', re.compile(r'/home/(?!you\b|runner\b)[A-Za-z0-9._-]+')),
    ('email', re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')),
    ('github-token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})')),
    ('api-key', re.compile(r'\bsk-[A-Za-z0-9_-]{20,}')),
    ('aws-key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('slack-token', re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{10,}')),
    ('google-api-key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('private-key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]
EMAIL_OK = re.compile(r'(?:@(example\.(com|org|net)|users\.noreply\.github\.com)$|^noreply@github\.com$)', re.I)


def load_terms(path):
    terms, allow = [], []
    with open(path, encoding='utf-8') as fh:
        for raw in fh:
            line = raw.rstrip('\n')
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if line.startswith('allow:'):
                allow.append(line[6:].strip())
            elif line.startswith('re:'):
                terms.append(('private-regex', re.compile(line[3:].strip(), re.I)))
            else:
                terms.append(('private-term', re.compile(re.escape(line.strip()), re.I)))
    return terms, allow


def scan_line(line, rules, allow):
    for text in allow:
        line = line.replace(text, ' ')
    hits = []
    for name, rx in rules:
        for m in rx.finditer(line):
            if name == 'email' and EMAIL_OK.search(m.group(0)):
                continue
            start = max(0, m.start() - 30)
            hits.append((name, line[start:m.end() + 30].strip()))
    return hits


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, errors='replace', check=True).stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--history', action='store_true')
    ap.add_argument('--terms-file')
    a = ap.parse_args(argv)
    rules, allow = list(PUBLIC), []
    if a.terms_file:
        try:
            terms, allow = load_terms(a.terms_file)
        except OSError as exc:
            print(f'cannot read the terms file: {exc}', file=sys.stderr)
            return 2
        rules += terms
    try:
        files = [f for f in git('ls-files', '-co', '--exclude-standard').splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        print('run inside a git repository', file=sys.stderr)
        return 2

    findings = []
    for path in files:
        try:
            data = open(path, 'rb').read()
        except OSError:
            continue
        if b'\0' in data:
            continue  # binary
        for i, line in enumerate(data.decode('utf-8', 'replace').splitlines(), 1):
            findings += [(f'{path}:{i}', name, text) for name, text in scan_line(line, rules, allow)]
        findings += [(f'{path} (file name)', name, text) for name, text in scan_line(path, rules, allow)]
    scanned = f'{len(files)} files'
    if a.history:
        commits = git('rev-list', '--all').split()
        for sha in commits:
            body = git('show', '--format=%B', '--patch', '--no-color', sha)
            for i, line in enumerate(body.splitlines(), 1):
                findings += [(f'commit {sha[:8]}:{i}', name, text) for name, text in scan_line(line, rules, allow)]
        idents = set(git('log', '--all', '--format=%an <%ae>%n%cn <%ce>').splitlines())
        for ident in sorted(idents):
            findings += [('identity', name, text) for name, text in scan_line(ident, rules, allow)]
        scanned += f', {len(commits)} commits, {len(idents)} identities'
    for where, name, text in findings:
        print(f'{where}: [{name}] {text}')
    mode = 'public patterns' + (' + private terms' if a.terms_file else '')
    print(f'leak_check ({mode}): {len(findings)} finding(s) in {scanned}')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())

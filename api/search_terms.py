"""Shared OR phrase search for cached and uncached workspace queries."""
import re
from fastapi import HTTPException


def normalize_terms(value):
    values = re.split(r'[,;\n]+', value) if isinstance(value, str) else value
    terms = list(dict.fromkeys(term.strip() for term in values if term.strip()))
    if len(terms) > 20 or any(len(term) > 200 for term in terms):
        raise HTTPException(400, 'Use at most 20 keywords, each up to 200 characters.')
    return terms


def search_filter(value, *, include_contact=True):
    terms = normalize_terms(value)
    if not terms:
        return '', []
    patterns = ['%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for term in terms]
    fields = ['r.raw_text', 'd.normalized_text']
    if include_contact:
        fields.append("coalesce(d.contact_name,'')")
    return ' AND (' + ' OR '.join(field + ' ILIKE ANY(%s)' for field in fields) + ')', [patterns] * len(fields)

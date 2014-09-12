# -*- coding: utf-8 -*-
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
from django.core.urlresolvers import reverse

from models import Word, Variant, PrimaryDefinition, Subdefinition, Example, Remark, Translation
from spelling import get_spelling_variations
from morphology import parse_morphology
from esperanto_sort import compare_esperanto_strings

def clean_search_term(search_term):
    # substitute ' if used, since e.g. vort' == vorto
    if search_term.endswith("'"):
        clean_term = search_term[:-1] + 'o'
    else:
        clean_term = search_term

    # strip any hyphens used, since we can't guarantee where they
    # will/will not appear
    clean_term = clean_term.replace('-', '')

    # all variants were stored lower case, so in case the user does
    # all caps:
    clean_term = clean_term.lower()

    return clean_term


def about(request):
    return render(request, 'about.html')


def index(request):
    # all requests are dispatched from here, to keep URLs simple

    if 'vorto' in request.GET:
        word = request.GET['vorto'].strip()
        return redirect('view_word', word)

    if u'serĉo' in request.GET:
        search_term = request.GET[u'serĉo'].strip()
        redirect_url = reverse('search_word')
        return redirect(redirect_url + u"?s=" + search_term)

    return render(request, 'index.html')


def view_word(request, word):
    # get the word
    try:
        word_obj = Word.objects.get(word=word)
    except Word.DoesNotExist:
        # search instead if this word doesn't exist
        return HttpResponseRedirect(u'/?serĉo=' + word)

    # get definitions
    definitions = PrimaryDefinition.objects.filter(word=word_obj)

    # get any examples, remarks, subdefinitions and subdefinition examples
    definition_trees = []
    for definition in definitions:
        examples = Example.objects.filter(definition=definition)

        remarks = Remark.objects.filter(definition=definition)

        # get subdefinitions with index and examples
        # e.g. [('ĉ the definition', ['blah', 'blah blah']
        subdefinitions = Subdefinition.objects.filter(root_definition=definition)
        subdefs_with_examples = []
        for i in range(subdefinitions.count()):
            sub_examples = Example.objects.filter(definition=subdefinitions[i])
            subdefs_with_examples.append((subdefinitions[i].definition,
                                          sub_examples))

        # we want to count according the esperanto alphabet for subdefinitions
        definition_trees.append((definition, remarks, examples,
                                 subdefs_with_examples))

    # get translations for every definition and subdefinition
    translations = []
    for definition in definitions:
        definition_translations = list(Translation.objects.filter(definition=definition))
        definition_translations = group_translations(definition_translations)

        subdefinitions = Subdefinition.objects.filter(root_definition=definition)
        subdefinitions_translations = []
        for subdefinition in subdefinitions:
            subdefinition_translations = list(Translation.objects.filter(definition=subdefinition))
            subdefinition_translations = group_translations(subdefinition_translations)

            if subdefinition_translations:
                subdefinitions_translations.append(subdefinition_translations)

        if definition_translations or subdefinitions_translations:
            translations.append((definition_translations, subdefinitions_translations))

    return render(request, 'word.html',
                  {'word': word, 'definitions': definition_trees,
                   'translations': translations})


def search_word(request):
    query = request.GET[u's'].strip()

    search_term = clean_search_term(query)

    # allow users to go directly to a word definition if we can find one
    if 'rekte' in request.GET:
        matches = precise_word_search(search_term)

        if matches:
            return redirect('view_word', matches[0].word)

    # if search term is stupidly long, truncate it
    if len(search_term) > 40:
        search_term = search_term[:40]

    word = clean_search_term(search_term)

    # substitute ' if used, since e.g. vort' == vorto
    if search_term.endswith("'"):
        word = search_term[:-1] + 'o'
    else:
        word = search_term

    # strip any hyphens used, since we can't guarantee where they
    # will/will not appear
    word = word.replace('-', '')
    # except if we start with a hyphen, which was probably deliberate
    if search_term.startswith('-'):
        word = '-' + word

    # all variants were stored lower case, so in case the user does
    # all caps:
    word = word.lower()

    matching_words = precise_word_search(word)

    # imprecise search, excluding those already found in the precise search
    similar_words = [term for term in imprecise_word_search(word)
                     if term not in matching_words]

    # get morphological parsing results
    # of form [['konk', 'lud'], ['konklud']]
    potential_parses = parse_morphology(word)

    # potential parses are weighted by likelihood, only show top two
    # since the rest are probably nonsensical
    potential_parses = potential_parses[:2]

    # get matching translations, ignoring changes we made for
    # esperanto words
    translations = translation_search(search_term)

    return render(request, 'search.html',
                  {'search_term':search_term,
                   'matching_words':matching_words,
                   'similar_words':similar_words,
                   'potential_parses':potential_parses,
                   'translations':translations})


def precise_word_search(word):
    """Find every possible term this word could be. Our variant table
    holds every possible conjugation and declension, so we just query
    that and remove duplicates.

    We return results in alphabetical order. However, the only way to
    get more than one result is if we have the same string in
    different cases, so really this just means lower case first.

    """
    matching_variants = Variant.objects.filter(variant=word)

    # find corresponding words, stripping duplicates
    matching_words = []
    for variant in matching_variants:
        if not variant.word in matching_words:
            matching_words.append(variant.word)

    # sort alphabetically
    compare = lambda x, y: compare_esperanto_strings(x.word, y.word)
    matching_words.sort(cmp=compare)

    return matching_words

def imprecise_word_search(word):
    """We generate alternative strings and also look them up in the
    dictionary. For very long words (13 letters or more) we generate
    too many alternatives so we only test the first 999 to keep sqlite
    happy.

    Results are returned in alphabetical order.

    """
    spelling_variations = get_spelling_variations(word)

    # limit for sqlite
    if len(spelling_variations) > 999:
        spelling_variations = spelling_variations[:999]

    # find matches
    matching_variants = Variant.objects.filter(variant__in=spelling_variations)

    # find corresponding words, stripping duplicates
    similar_words = []
    for variant in matching_variants:
        if (not variant.word in similar_words):
            similar_words.append(variant.word)

    # sort spelling variants into alphabetical order
    compare = lambda x, y: compare_esperanto_strings(x.word, y.word)
    similar_words.sort(cmp=compare)

    return similar_words

def translation_search(search_term):
    translations = list(Translation.objects.filter(translation=search_term))

    return group_translations(translations)

def group_translations(translations):
    """Given a list of translations, group into a list of lists where each
    sublist only contains translations of one language. Assumes the list is
    already sorted by language.

    """
    if not translations:
        return []

    translations.sort(key=(lambda t: t.language), cmp=compare_esperanto_strings)

    grouped_translations = [[translations[0]]]
    for translation in translations[1:]:
        if translation.language == grouped_translations[-1][-1].language:
            grouped_translations[-1].append(translation)
        else:
            grouped_translations.append([translation])

    return grouped_translations

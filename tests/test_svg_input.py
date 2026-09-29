import math

import pytest
from defusedxml.common import DefusedXmlException
from shapely.geometry import Polygon

from inlay_planner.svg_input import parse_svg, transform, point


def svg(body, extra='', size='width="30mm" height="20mm" viewBox="0 0 30 20"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {size} {extra}>{body}</svg>'


def areas(result):
    return sum(Polygon(p['outer'], p['holes']).area for r in result['regions'] for p in r['polygons'])


def test_physical_units_y_flip_and_holes():
    r = parse_svg(svg('<path fill-rule="evenodd" d="M2 3 H12 V13 H2 Z M4 5 H10 V11 H4 Z"/>'))
    assert not r['diagnostics']
    assert areas(r) == pytest.approx(64)
    assert r['bounds'] == pytest.approx([2,7,12,17])
    assert len(r['regions'][0]['polygons'][0]['holes']) == 1


@pytest.mark.parametrize('rule,reverse,expected', [('nonzero',False,100),('nonzero',True,64),('evenodd',False,64)])
def test_svg_winding_semantics(rule, reverse, expected):
    inner = 'M4 5 V11 H10 V5 Z' if reverse else 'M4 5 H10 V11 H4 Z'
    r = parse_svg(svg(f'<path fill-rule="{rule}" d="M2 3 H12 V13 H2 Z {inner}"/>'))
    assert areas(r) == expected


def test_nested_transforms_and_inherited_fill():
    r = parse_svg(svg('<g transform="translate(2 3)" fill-rule="evenodd"><g transform="scale(2)"><rect id="detail" width="4" height="2"/></g></g>'))
    assert areas(r) == 32
    assert r['bounds'] == [2,13,10,17]
    assert r['regions'][0]['id'] == 'detail'
    assert point((1,0), transform('translate(2 3) rotate(90)')) == pytest.approx((2,4))


def test_mixed_physical_units_and_assumed_size():
    r = parse_svg(svg('<rect width="96" height="96"/>',size='width="1in" height="25.4mm" viewBox="0 0 96 96"'))
    assert r['size'] == [25.4,25.4]
    assert areas(r) == pytest.approx(25.4**2)
    assert not r['size_assumed']
    assert parse_svg(svg('<rect width="1" height="1"/>',size='viewBox="0 0 30 20"'))['size_assumed']


def test_arc_and_bezier_flattening_converges():
    circle = svg('<circle cx="10" cy="10" r="5"/>')
    r = parse_svg(circle, .001)
    assert abs(areas(r)-math.pi*25) < .04
    rounded = parse_svg(svg('<rect x="2" y="2" width="10" height="8" rx="2"/>'), .0001)
    assert areas(rounded) == pytest.approx(80-16+4*math.pi,abs=.01)
    cubic = parse_svg(svg('<path d="M0,0 C0,10 10,10 10,0 Z"/>'), .001)
    assert areas(cubic) == pytest.approx(60,abs=.02)


@pytest.mark.parametrize('body', [
    '<path d="M0 0 L10 0 L10 10"/>',
    '<path d="M0 0 L10 10 L0 10 L10 0 Z"/>',
    '<rect width="10" height="10" fill="none" stroke="black"/>',
    '<text x="2" y="3">Hello</text>',
    '<image href="https://example.com/trap.png"/>',
    '<rect width="10" height="10" filter="url(#filter)"/>',
    '<use href="file:///etc/passwd"/>',
    '<g opacity=".5"><rect width="10" height="10"/></g>',
])
def test_unsupported_content_stays_explicit_and_selectable(body):
    r = parse_svg(svg(body+'<rect id="good" x="15" width="5" height="5"/>'))
    assert r['diagnostics']
    assert any(x['supported'] is False for x in r['regions'])
    assert any(x['id']=='good' and x['supported'] for x in r['regions'])
    assert areas(r) == 25


def test_internal_references_and_cycles():
    r = parse_svg(svg('<defs><rect id="part" width="2" height="3"/></defs><use id="copy" href="#part" x="5" y="2"/>'))
    assert areas(r) == 6
    assert r['regions'][0]['id'] == 'copy/part'
    r = parse_svg(svg('<defs><use id="loop" href="#loop"/></defs><use href="#loop"/>'))
    assert r['diagnostics']


def test_external_entities_and_nonfinite_inputs_rejected():
    with pytest.raises(DefusedXmlException):
        parse_svg('<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg width="1" height="1">&x;</svg>')
    with pytest.raises(ValueError):
        parse_svg(svg('<rect width="1" height="1"/>'), float('nan'))


def test_numeric_lists_keep_valid_commas_whitespace_signs_and_exponents():
    source = svg('<polygon points="+0,0 1e1,0 10,1.e1 .0,10" '
                 'transform="translate( 2,\t3 ) scale( .5 1e0 )"/>',
                 size='width="30mm" height="20mm" viewBox="0, 0, 3e1, 2e1"')
    result = parse_svg(source)
    assert not result['diagnostics']
    assert areas(result) == pytest.approx(50)
    assert result['bounds'] == pytest.approx([2, 7, 7, 17])


@pytest.mark.parametrize('arguments', ['1,,2', '1,', '1 2garbage', '1.2.3', '1e309'])
def test_bad_transform_number_lists_diagnose_only_the_affected_region(arguments):
    result = parse_svg(svg(f'<rect id="bad" width="5" height="5" transform="translate({arguments})"/>'
                           '<rect id="good" x="15" width="2" height="2"/>'))
    assert result['diagnostics']
    assert any(r['id'] == 'bad' and not r['supported'] for r in result['regions'])
    assert any(r['id'] == 'good' and r['supported'] for r in result['regions'])
    assert areas(result) == 4


@pytest.mark.parametrize('size', [
    'width="1e309mm" height="20mm"',
    'width="30mm" height="20mm" viewBox=""',
    'width="30mm" height="20mm" viewBox="0 0 1e309 20"',
    'width="30mm" height="20mm" viewBox="0 0 1e-320 20" preserveAspectRatio="none"',
])
def test_invalid_or_nonfinite_viewport_never_produces_geometry(size):
    with pytest.raises(ValueError):
        parse_svg(svg('<rect width="5" height="5"/>', size=size))


def test_group_fill_opacity_is_inherited_and_can_be_overridden():
    result = parse_svg(svg('<g fill-opacity=".5"><rect id="opaque" fill-opacity="1" '
                           'width="5" height="5"/></g>'))
    assert not result['diagnostics']
    assert areas(result) == 25


def test_display_none_cannot_be_overridden_by_visible_child():
    result = parse_svg(svg('<g display="none"><rect id="hidden" display="inline" '
                           'visibility="visible" width="5" height="5"/></g>'
                           '<rect id="good" x="15" width="2" height="2"/>'))
    assert not result['diagnostics']
    assert [r['id'] for r in result['regions']] == ['good']
    assert areas(result) == 4


def test_hidden_use_allows_visible_referenced_descendant():
    result = parse_svg(svg('<defs><g id="part"><rect id="visible" visibility="visible" '
                           'width="5" height="5"/></g></defs>'
                           '<use id="copy" visibility="hidden" href="#part"/>'))
    assert not result['diagnostics']
    assert areas(result) == 25
    assert result['regions'][0]['id'] == 'copy/visible'


def test_group_compositing_in_inline_style_remains_effective():
    result = parse_svg(svg('<g style="mask:url(#m)"><rect style="mask:none" '
                           'width="5" height="5"/></g>'
                           '<rect id="good" x="15" width="2" height="2"/>'))
    assert result['diagnostics']
    assert areas(result) == 4


@pytest.mark.parametrize('style', ['transform:translate(8px,2px)',
                                  'transform-origin:center', 'width:12px'])
def test_unsupported_css_geometry_is_not_silently_ignored(style):
    result = parse_svg(svg(f'<rect id="bad" width="5" height="5" style="{style}"/>'
                           '<rect id="good" x="15" width="2" height="2"/>'))
    assert result['diagnostics']
    assert any(r['id'] == 'bad' and not r['supported'] for r in result['regions'])
    assert any(r['id'] == 'good' and r['supported'] for r in result['regions'])
    assert areas(result) == 4


@pytest.mark.parametrize('radius', ['0,3', '3,0'])
def test_zero_axis_arc_is_a_straight_segment(radius):
    result = parse_svg(svg(f'<path d="M0,0 A{radius} 0 0 1 10,0 L10,10 L0,10 Z"/>'))
    assert not result['diagnostics']
    assert areas(result) == 100


def test_undersized_arc_radii_are_corrected_before_flattening():
    # Endpoints 100 mm apart force both requested 1 mm radii up to 50 mm.
    # The curved vertices form a semicircle; their chord midpoint sagitta has
    # a direct analytic bound independent of svg.path's radius_scale field.
    tolerance = .001
    result = parse_svg(svg('<path d="M0,0 A1,1 0 0 1 100,0 Z"/>',
                           size='width="100mm" height="100mm" viewBox="0 0 100 100"'), tolerance)
    assert not result['diagnostics']
    ring = result['regions'][0]['polygons'][0]['outer']
    chord_errors = []
    for a, b in zip(ring, ring[1:]):
        if math.dist(a, b) > 99:  # The straight closing diameter.
            continue
        midpoint = ((a[0]+b[0])/2, (a[1]+b[1])/2)
        chord_errors.append(50-math.dist(midpoint, (50, 100)))
    assert chord_errors
    assert 0 <= max(chord_errors) <= tolerance + 1e-10
    assert result['flattening_tolerance'] == tolerance


def test_circle_flattening_tolerance_is_in_physical_transformed_units():
    # A source radius of 1 becomes 20 mm through the SVG viewport and group
    # transforms. All chord errors must be bounded in those final millimetres.
    tolerance = .002
    result = parse_svg(svg('<g transform="translate(2 2) scale(2)"><circle cx="2" cy="2" r="1"/></g>',
                           size='width="100mm" height="100mm" viewBox="0 0 10 10"'), tolerance)
    assert not result['diagnostics']
    ring = result['regions'][0]['polygons'][0]['outer']
    errors = [20-math.dist(((a[0]+b[0])/2, (a[1]+b[1])/2), (60, 40))
              for a, b in zip(ring, ring[1:])]
    assert 0 <= max(errors) <= tolerance + 1e-10
    assert result['flattening_tolerance'] == tolerance


def test_unresolvable_curve_budget_is_diagnosed_without_relaxing_tolerance():
    tolerance = 1e-30
    result = parse_svg(svg('<circle cx="10" cy="10" r="5"/>'), tolerance)
    assert result['diagnostics']
    assert not any(r['supported'] for r in result['regions'])
    assert result['flattening_tolerance'] == tolerance


def test_later_artwork_enlargement_needs_a_divided_source_tolerance():
    source = svg('<circle cx="10" cy="10" r="5"/>')
    enlargement = 7.5
    final_budget = .02/32

    def largest_final_chord_error(result):
        ring = result['regions'][0]['polygons'][0]['outer']
        return max(enlargement*(5-math.dist(((a[0]+b[0])/2, (a[1]+b[1])/2), (10, 10)))
                   for a, b in zip(ring, ring[1:]))

    # The importer cannot know a later placement scale. This control exposes
    # the old caller error, and verifies the source-space budget it must pass.
    coarse = parse_svg(source, final_budget)
    assert largest_final_chord_error(coarse) > final_budget
    refined = parse_svg(source, final_budget/enlargement)
    assert largest_final_chord_error(refined) <= final_budget + 1e-10
    assert refined['flattening_tolerance'] == final_budget/enlargement

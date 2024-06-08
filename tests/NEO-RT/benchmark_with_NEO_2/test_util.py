def test_replace_template():
    from util import fill_template
    template = 'bla {var1} {var2} bli'
    vars = {
        'var1': 1,
        'var2': 'blu'
    }
    text = fill_template(template, vars)
    assert(text == 'bla 1 blu bli')

test_replace_template()

"""
Python generators for pipelining ROOT-object wrappers.

Python generators help to process ROOT-objects Many generators are predefined
here. They generalize the operations and deliver utility functions.
All generators and functions in this module should help to easy the work within
the ``run()`` method of a tool, but they can also be used anywhere.
Chaining (or 'packaging') of elementary generators is done in the last quarter
of this module, starting with the ``fs_filter_sort_load`` generator.

To learn more about python generators, please have a look at
http://www.dabeaz.com/generators/index.html
"""

################################################################### utility ###
import collections
import itertools

import analysis
import diskio
import wrappers
import operator


def _iterableize(obj_or_iterable):
    """provides iterable for [obj OR iterable(obj)]"""
    if (isinstance(obj_or_iterable, collections.Iterable)
            and not type(obj_or_iterable) == str):
        for o in obj_or_iterable:
            yield o
    else:
        yield obj_or_iterable


def debug_printer(iterable, print_obj=True):
    """
    Print objects and their type on flying by. Object printing can be disabled.

    :param iterable:    An iterable with objects
    :yields:            same as input
    """
    for obj in iterable:
        print 'DEBUG: debug_printer: obj type: ', type(obj)
        if print_obj:
            print 'DEBUG: debug_printer: obj:      ', obj
        yield obj


def imap_conditional(iterable, keyfunc, func, **func_kws):
    """
    Like itertools.imap, but only applying func if keyfunc evaluates to True. 

    >>> def change_sign(value):
    ...     return -value
    >>> def is_even_val(v):
    ...     return v%2 == 0
    >>> list(imap_conditional([1, 2, 3, 4, 5, 6, 7], is_even_val, change_sign))
    [1, -2, 3, -4, 5, -6, 7]
    """
    for val in iterable:
        if keyfunc(val):
             yield func(val, **func_kws)
        else:
             yield val


def switch(iterable, keyfunc, generator, **gen_kws):
    """
    Switches items to go through generator or not.

    The order of items is preserved if ``generator`` does not reshuffle them. 
    
    :param iterable:    input iterable
    :param keyfunc:     function, called with items from iterable. If an item
                        evaluates to True, the item goes through generator, 
                        otherwise it's yielded as is.
    :param generator:   generator function, through with items are passed if 
                        ``keyfunc`` evaluates to True for them.

    >>> def gen_change_sign(values):
    ...     for v in values:
    ...         yield -v
    >>> def is_even_val(v):
    ...     return v%2 == 0
    >>> list(switch([1, 2, 3, 4, 5, 6, 7], is_even_val, gen_change_sign))
    [1, -2, 3, -4, 5, -6, 7]
    """
    passing_queue = []

    def pre(it):
        for newval in it:
            if keyfunc(newval):
                yield newval
            else:
                passing_queue.append(newval)

    def post(gen):
        for gen_val in gen:
            while passing_queue:            # empty passing items first
                yield passing_queue.pop(0)
            yield gen_val
        while passing_queue:                # empty leftover items
            yield passing_queue.pop(0)

    return post(generator(pre(iterable), **gen_kws))


def consume_n_count(iterable):
    """
    Walks over iterable and counts number of items.

    :returns:   integer
    """
    count = 0
    for _ in iterable:
        count += 1
    return count


def filter_active_samples(wrps):
    return itertools.ifilter(
        lambda w: (not analysis.active_samples)
                  or w.sample in analysis.active_samples,
        wrps
    )


def sort(wrps, key_list=None):
    """
    Sort stream after items in key_list. Loads full stream into memory.

    :param wrps:        Wrapper iterable
    :param key_list:    (List of) token(s) after which the stream is sorted.
                        First item has highest importance. If ``None``, then
                        ``settings.wrp_sorting_keys`` list is used.
    :returns:           sorted list of wrappers.
    """
    if not key_list:
        key_list = settings.wrp_sorting_keys
    # python sorting is stable: Just sort by reversed key_list:
    wrps = list(wrps)
    for key in reversed(list(_iterableize(key_list))):
        try:
            wrps = sorted(wrps, key=operator.attrgetter(key))
        except AttributeError:
            print 'WARNING Sorting by "%s" failed.' % key
    return wrps


def group(wrps, key_func=None):
    """
    Clusters stream into groups. wrps should be sorted.

    :param wrps:        Wrapper iterable
    :param key_func:    callable to group the wrappers. If ``None``, then
                        ``lambda w: w.in_file_path`` is used.
    :yields:            WrapperWrapper

    **Example:** This is neccessary before stacking, in order to have only
    same-observable-histograms stacked together::

        # wrappers has the names ['h1', 'h1', 'h2', 'h2']
        wrappers = group(wrappers)
        # result is like: [ ('h1', 'h1'), ('h2', 'h2') ]
    """
    if not key_func:
        key_func = lambda w: w.in_file_path
    for k, g in itertools.groupby(wrps, key_func):
        yield wrappers.WrapperWrapper(list(g), name=k)


def interleave(*grouped_wrps):
    """
    Like itertools.izip, but chains inner packaging. Useful before canvasses.

    ((a,b),(c,d)), ((1,2),(3,4)) => ((a,b,1,2), (c,d,3,4))

    :param grouped_wrps:    grouped iterators (multi-argument)
    :yields:                generator object
    """
    zipped = itertools.izip(grouped_wrps)
    for grp in zipped:
        yield itertools.chain(*grp)


def split_data_bkg_sig(wrps):
    """
    Split stream into data and two mc streams.

    This function splits the wrapper stream into three by the ``is_data`` and 
    ``is_signal`` attributes of the given wrappers.

    :param wrps:        Wrapper iterable
    :returns:           three wrapper iterators: ``(data, bkg, sig)``
    """
    wrps_a, wrps_b, wrps_c = itertools.tee(wrps, 3)
    dat = itertools.ifilter(lambda w: w.is_data, wrps_a)
    sig = itertools.ifilter(lambda w: w.is_signal, wrps_b)
    bkg = itertools.ifilter(lambda w: not (w.is_data or w.is_signal), wrps_c)
    return dat, bkg, sig


################################################################ operations ###
import operations as op


def _generate_op(op_func):
    """
    Transforms an operation with one argument into a generator.

    :param op_func: callable
    :returns:       generator

    **Example:** The ``lumi`` and ``integral`` operations are generatorized
    below (notice that ``w1``,``w2`` and ``w3`` are iterables):

    >>> from ROOT import TH1I
    >>> from varial.wrappers import HistoWrapper
    >>> h1 = TH1I('h1', '', 2, .5, 4.5)
    >>> h1.Fill(1)
    1
    >>> h1.Fill(3)
    2
    >>> w1 = [HistoWrapper(h1, lumi=2.)]
    >>> gen_lumi = _generate_op(op.lumi)
    >>> w2 = list(gen_lumi(w1))
    >>> w2[0].float
    2.0
    >>> gen_int = _generate_op(op.integral)
    >>> w3 = list(gen_int(w1))
    >>> w3[0].float
    2.0
    >>> w4 = list(gen_int(w1, use_bin_width=True))
    >>> w4[0].float
    4.0
    """
    def gen_op(wrps, *args, **kws):
        for wrp in wrps:
            yield op_func(wrp, *args, **kws)
    return gen_op


def _generate_op_noex(op_func):
    """
    Same as ``_generate_op`` but catches ``op.WrongInputError``.

    >>> from varial.wrappers import FloatWrapper
    >>> gen_noex_mv_in = _generate_op_noex(op.mv_in)
    >>> w1 = FloatWrapper(2.0)
    >>> w2 = list(gen_noex_mv_in([w1]))  # WrongInputError is catched
    >>> w1 == w2[0]
    True
    """
    def gen_op_noex(wrps, *args, **kws):
        for wrp in wrps:
            try:
                yield op_func(wrp, *args, **kws)
            except op.WrongInputError:
                yield wrp
    return gen_op_noex


#TODO: write doc for these into _generate_op and _generate_op_noex
gen_add_wrp_info        = _generate_op(op.add_wrp_info)
gen_stack               = _generate_op(op.stack)
gen_sum                 = _generate_op(op.sum)
gen_merge               = _generate_op(op.merge)
gen_prod                = _generate_op(op.prod)
gen_div                 = _generate_op(op.div)
gen_lumi                = _generate_op(op.lumi)
gen_norm_to_lumi        = _generate_op(op.norm_to_lumi)
gen_norm_to_integral    = _generate_op(op.norm_to_integral)
gen_copy                = _generate_op(op.copy)
gen_mv_in               = _generate_op(op.mv_in)
gen_rebin               = _generate_op(op.rebin)
gen_trim                = _generate_op(op.trim)
gen_integral            = _generate_op(op.integral)
gen_int_l               = _generate_op(op.int_l)
gen_int_r               = _generate_op(op.int_r)
gen_eff                 = _generate_op(op.eff)
gen_th2_projection_x    = _generate_op(op.th2_projection_x)
gen_th2_projection_y    = _generate_op(op.th2_projection_y)

gen_noex_norm_to_lumi       = _generate_op_noex(op.norm_to_lumi)
gen_noex_norm_to_integral   = _generate_op_noex(op.norm_to_integral)
gen_noex_th2_projection_x   = _generate_op_noex(op.th2_projection_x)
gen_noex_th2_projection_y   = _generate_op_noex(op.th2_projection_y)


def gen_norm_to_data_lumi(wrps):
    return gen_prod(
        itertools.izip(
            gen_norm_to_lumi(wrps),
            itertools.repeat(analysis.data_lumi_sum_wrp())
        )
    )


def gen_make_eff_graphs(wrps,
                        postfix_sub='_sub',
                        postfix_tot='_tot',
                        new_postfix='_eff',
                        yield_everything=False,
                        pair_func=lambda w, l: w.in_file_path[:-l]):
    """
    Makes efficiency graphs and interleaves them into a sorted stream.

    Searches for histgrams ending with ``postfix_sub`` and ``postfix_tot``. On
    finding a matching pair, it creates an efficiency graph. The graphs are
    interleaved in a sorted stream without disturbing the order needed for 
    plotting. 

    :param wrps:        Wrapper iterable
    :param postfix_sub: str, search token for histograms of passing data,
                        default: ``_sub``
    :param postfix_tot: str, search token for histograms of all data,
                        default: ``_tot``
    :yields:            Wrapper (simply forwarding), GraphWrapper
    """

    len_postfix_sub = len(postfix_sub)
    len_postfix_tot = len(postfix_tot)

    def rename(w):
        w.in_file_path = w.in_file_path[:-len_postfix_sub] + new_postfix
        w.name = w.in_file_path.split('/')[-1]
        return w

    subs, tots = {}, {}
    res = []
    for wrp in wrps:
        if yield_everything:
            yield wrp
        if wrp.name.endswith(postfix_sub):
            t = pair_func(wrp, len_postfix_sub)
            if t in tots:
                res.append(rename(op.eff((wrp, tots.pop(t)))))
            else:
                subs[t] = wrp
        elif wrp.name.endswith(postfix_tot):
            t = pair_func(wrp, len_postfix_tot)
            if t in subs:
                res.append(rename(op.eff((subs.pop(t), wrp))))
            else:
                tots[t] = wrp
        elif not yield_everything:  # do not yield everything twice
            yield wrp
        if res and not (subs or tots):
            for _ in xrange(len(res)):
                yield res.pop(0)


def gen_make_th2_projections(wrps, keep_th2=True):
    """
    Makes x- and y-projections of TH2 hists and interleaves them in the stream.

    :param wrps:        Wrapper iterable
    :param keep_th2:    bool, if False the TH2 hists are dropped.
    """
    token = lambda w: w.in_file_path
    current_token = None
    x_buf, y_buf = [], []

    for wrp in wrps:
        if current_token:
            if token(wrp) != current_token:
                current_token = None
                while x_buf:
                    yield x_buf.pop(0)
                while y_buf:
                    yield y_buf.pop(0)
        if 'TH2' in wrp.type:
            current_token = token(wrp)
            x_buf.append(op.th2_projection_x(wrp))
            y_buf.append(op.th2_projection_y(wrp))
            if keep_th2:
                yield wrp
        else:
            yield wrp

    while x_buf:
        yield x_buf.pop(0)
    while y_buf:
        yield y_buf.pop(0)



############################################################### load / save ###
import settings
import glob
import os


def resolve_file_pattern(pattern='./*.root'):
    """
    Resolves search pattern(s) for files.

    Raises RuntimeError if no files could be found for a pattern.

    :param pattern: string or list of strings.
    :returns:       List of filenames
    """
    if type(pattern) is str:
        pattern = [pattern]
    result = list(glob.glob(pat) for pat in pattern)
    for pat, res in itertools.izip(pattern, result):
        if not res or not all(os.path.isfile(f) for f in res):
            raise RuntimeError('No file(s) found for pattern: %s' % pat)

    return list(itertools.chain.from_iterable(result))


def fs_content():
    """
    Searches for samples and yields aliases.

    :yields:   FileServiceAlias
    """
    for alias in analysis.fs_aliases:
        yield alias


def dir_content(pattern='./*.root'):
    """
    Proxy of diskio.generate_aliases(directory)

    :yields:   Alias
    """
    return diskio.generate_aliases_list(resolve_file_pattern(pattern))


def load(aliases):
    """
    Loads histograms in histowrappers for aliases.

    :param aliases: Alias iterable
    :yields:        HistoWrapper
    """
    for alias in aliases:
        yield diskio.load_histogram(alias)


def save(wrps, filename_func, suffices=None, write_complete_wrp=False):
    """
    Saves passing wrps to disk, plus .info file with the wrapper infos.

    :param wrps:            Wrapper iterable
    :param filename_func:   callable that returns path and filename without
                            suffix.
    :param suffices:        list of suffices

    **Example:** ::

        save(
            wrappers,
            lambda wrp: OUTPUT_DIR + wrp.name,
            [.root, .png]           # DEFAULT: settings.rootfile_postfixes
        )
    """
    if not suffices:
        suffices = settings.rootfile_postfixes
    for wrp in wrps:
        filename = filename_func(wrp)
        if write_complete_wrp:
            diskio.write(wrp, filename, suffices)
        else:
            filename = diskio.prepare_basename(filename)
            with open(filename+'.info', 'w') as f:
                diskio._write_wrapper_info(wrp, f)
            for suffix in suffices:
                wrp.primary_object().SaveAs(filename + suffix)
        yield wrp


################################################################## plotting ###
import rendering as rnd


def apply_fillcolor(wrps, colors=None):
    """
    Uses ``histo.SetFillColor``. Colors from analysis module, if not given.

    :param wrps:    HistoWrapper iterable
    :param colors:  Integer list
    :yields:        HistoWrapper
    """
    n = 0
    for wrp in wrps:
        if colors:
            color = colors[n % len(colors)]
            n += 1
        else:
            color = analysis.get_color(wrp.legend or wrp.sample)
        if color:
            wrp.primary_object().SetFillColor(color)
        yield wrp


def apply_linecolor(wrps, colors=None):
    """
    Uses ``histo.SetLineColor``. Colors from analysis module, if not given.

    :param wrps:    HistoWrapper iterable
    :param colors:  Integer list
    :yields:        HistoWrapper
    """
    n = 0
    for wrp in wrps:
        if colors:
            color = colors[n % len(colors)]
            n += 1
        else:
            color = analysis.get_color(wrp.legend or wrp.sample)
        if color:
            wrp.primary_object().SetLineColor(color)
        yield wrp


def apply_linewidth(wrps, linewidth=2):
    """
    Uses ``histo.SetLineWidth``. Default is 2.

    :param wrps:        HistoWrapper iterable
    :param linewidth:  argument for SetLineWidth
    :yields:            HistoWrapper
    """
    for wrp in wrps:
        wrp.primary_object().SetLineWidth(linewidth)
        yield wrp


def apply_fillstyle(wrps, fillstyle=3444):
    """
    Uses ``histo.SetLineWidth``. Default is 2.

    :param wrps:        HistoWrapper iterable
    :param fillstyle:   integer argument for SetFillStyle
    :yields:            HistoWrapper
    """
    for wrp in wrps:
        wrp.primary_object().SetFillStyle(fillstyle)
        yield wrp


def apply_markercolor(wrps, colors=None):
    """
    Uses ``histo.SetMarkerColor``. Colors from analysis module, if not given.

    :param wrps:    HistoWrapper iterable
    :param colors:  Integer list
    :yields:        HistoWrapper
    """
    n = 0
    for wrp in wrps:
        if colors:
            color = colors[n % len(colors)]
            n += 1
        else:
            color = analysis.get_color(wrp.legend or wrp.sample)
        if color:
            wrp.primary_object().SetMarkerColor(color)
        yield wrp


def make_canvas_builder(grps):
    """
    Yields instanciated CanvasBuilders.

    :param grps:    grouped or ungrouped Wrapper iterable
                    if grouped: on canvas for each group
    :yields:        CanvasBuilder instance
    """
    for grp in grps:
        grp = _iterableize(grp)
        yield rnd.CanvasBuilder(grp)


def decorate(wrps, decorators=()):
    """
    Decorate any iterable with a list of decorators.

    :param wrps:        Wrapper (or CanvasBuilder) iterable
    :param decorators:  list of decorator classes.
    :yields:            Wrapper (or CanvasBuilder)

    **Example:** ::

        result = decorate([CanvasBuilder, ...], [Legend, TextBox])
        # result = [TextBox(Legend(CanvasBuilder)), ...]
    """
    for wrp in wrps:
        for dec in decorators:
            wrp = dec(wrp)
        yield wrp


def build_canvas(bldrs):
    """
    Calls the ``build_canvas()`` method and returns the result.

    :param bldrs:   CanvasBuilder iterable
    :yields:        CanvasWrapper
    """
    for bldr in bldrs:
        yield bldr.build_canvas()


def switch_log_scale(cnvs, y_axis=True, x_axis=False):
    """
    Sets main_pad in canvases to logscale.
    
    :param cnvs:    CanvasWrapper iterable
    :param x_axis:  boolean for x axis
    :param y_axis:  boolean for y axis
    :yields:        CanvasWrapper
    """
    for cnv in cnvs:
        assert isinstance(cnv, rnd.wrappers.CanvasWrapper)
        if x_axis:
            cnv.main_pad.SetLogx(1)
        else:
            cnv.main_pad.SetLogx(0)
        if y_axis:
            cnv.first_drawn.SetMinimum(cnv.y_min_gr_0 * 0.5)
            cnv.main_pad.SetLogy(1)
        else:
            cnv.first_drawn.SetMinimum(cnv.y_min)
            cnv.main_pad.SetLogy(0)
        yield cnv


################################################### application & packaging ###


def fs_filter_sort_load(filter_keyfunc=None, sort_keys=None):
    """
    Packaging of filtering, sorting and loading.

    :param filter_dict: see function filter(...) above
    :param sort_keys:   see function sort(...) above
    :yields:            HistoWrapper

    **Implementation:** ::

        wrps = fs_content()
        wrps = filter(wrps, filter_dict)
        wrps = sort(wrps, key_list)
        return load(wrps)
    """
    wrps = fs_content()
    wrps = itertools.ifilter(filter_keyfunc, wrps)
    wrps = sort(wrps, sort_keys)
    return load(wrps)


def fs_filter_active_sort_load(filter_keyfunc=None, sort_keys=None):
    """
    Just as fs_filter_sort_load, but also filters for active samples.
    """
    wrps = fs_content()
    wrps = filter_active_samples(wrps)
    wrps = itertools.ifilter(filter_keyfunc, wrps)
    wrps = sort(wrps, sort_keys)
    return load(wrps)


def mc_stack(wrps, merge_mc_key_func=None):
    """
    Delivers only MC stacks, feed only with MC.

    :param wrps:                Iterables of HistoWrapper (grouped)
    :param merge_mc_key_func:   key function for python sorted(...), default
                                tries to sort after stack position
    :yields:                    StackWrapper
    """
    if not merge_mc_key_func:
        merge_mc_key_func = lambda w: analysis.get_stack_position(w.sample)
    for grp in wrps:

        # merge mc samples (merge also normalizes to lumi = 1.)
        mc_sorted = sorted(grp, key=merge_mc_key_func)
        mc_groupd = group(mc_sorted, merge_mc_key_func)
        mc_merged = (op.merge(g) for g in mc_groupd)
        mc_colord = apply_fillcolor(mc_merged)

        # stack mc
        stack = op.stack(mc_colord)
        yield stack


def fs_mc_stack(filter_keyfunc=None, merge_mc_key_func=None):
    """
    Delivers only MC stacks, no data, from fileservice.

    :param filter_dict:         see function filter(...) above
    :param merge_mc_key_func:   key function for python sorted(...), default
                                tries to sort after stack position
    :yields:                    StackWrapper
    """
    loaded = fs_filter_active_sort_load(filter_keyfunc)
    grouped = group(loaded)
    return mc_stack(grouped, merge_mc_key_func)


def mc_stack_n_data_sum(wrps, merge_mc_key_func=None, use_all_data_lumi=False):
    """
    Stacks MC histos and merges data, input needs to be sorted and grouped.

    Yields tuples of an MC stack, signal histograms, and a data histogram, if 
    all kinds of data are present. Raises an exception if no histograms are
    given at all.

    :param wrps:                Iterables of HistoWrapper (grouped)
    :param merge_mc_key_func:   key function for python sorted(...), default
                                tries to sort after stack position
    :yields:                    WrapperWrapper of wrappers for plotting
    """
    if not merge_mc_key_func:
        merge_mc_key_func = lambda w: analysis.get_stack_position(w.sample)

    for grp in wrps:

        # split stream
        dat, bkg, sig = split_data_bkg_sig(grp)

        # data
        dat_sum = None
        try:
            dat_sum = op.sum(dat)
        except op.TooFewWrpsError:
            if settings.debug_mode:
                print 'DEBUG generators.mc_stack_n_data_sum(..): '\
                      'No data histograms present!'
        if dat_sum and not use_all_data_lumi:
            data_lumi = op.lumi(dat_sum)
        else:
            data_lumi = analysis.data_lumi_sum_wrp()

        # background (op.merge normalizes to lumi = 1.)
        bkg = sorted(bkg, key=merge_mc_key_func)
        is_2d = bkg and 'TH2' in bkg[0].type
        bkg = group(bkg, merge_mc_key_func)
        bkg = (op.merge(g) for g in bkg)
        bkg = apply_fillcolor(bkg)
        bkg = apply_linecolor(bkg, [1])
        bkg = gen_prod(itertools.izip(bkg, itertools.repeat(data_lumi)))
        bkg_stk = None
        try:
            if is_2d:
                bkg_stk = op.sum(bkg)
            else:
                bkg_stk = op.stack(bkg)
        except op.TooFewWrpsError:
            if settings.debug_mode:
                print 'DEBUG generators.mc_stack_n_data_sum(..): '\
                      'No background histograms present!'

        # signal
        sig = apply_linecolor(sig)
        sig_lst = list(sig)
        if not sig_lst and settings.debug_mode:
            print 'DEBUG generators.mc_stack_n_data_sum(..): '\
                  'No signal histograms present!'

        # return in order for plotting: bkg, signals, data
        res = [bkg_stk] + sig_lst + [dat_sum]
        res = filter(None, res)
        if res:
            yield wrappers.WrapperWrapper(res, name=grp.name)
        else:
            raise op.TooFewWrpsError('No histograms present!')


def fs_mc_stack_n_data_sum(filter_keyfunc=None, merge_mc_key_func=None):
    """
    The full job to stacked histos and data, directly from fileservice.

    The output are tuples of MC stacks and data histograms.

    :param filter_dict:         see function filter(...) above
    :param merge_mc_key_func:   key function for python sorted(...), default
                                tries to sort after stack position
    :yields:                    tuples of wrappers for plotting
    """
    loaded = fs_filter_active_sort_load(filter_keyfunc)
    grouped = group(loaded)     # default: group by in_file_path
    return mc_stack_n_data_sum(grouped, merge_mc_key_func, True)


def canvas(grps, decorators=()):
    """
    Packaging of canvas builder, decorating, callback and canvas building.

    :param grps:            grouped or ungrouped Wrapper iterable
                            if grouped: on canvas for each group
    :param decorators:      see function decorate(...) above
    :yields:                CanvasWrapper
    """
    grps = make_canvas_builder(grps)            # a builder for every group
    grps = decorate(grps, decorators)           # apply decorators
    return build_canvas(grps)                   # and do the job


def save_canvas_lin_log(cnvs, filename_func, log_only_1d_hists=True):
    """
    Saves canvasses, switches to logscale, saves again.

    :param cnvs:            CanvasWrapper iterable
    :param filename_func:   see function save(...) above
    :yields:                CanvasWrapper
    """
    cnvs = save(
        cnvs,
        lambda c: filename_func(c) + '_lin'
    )
    if log_only_1d_hists:
        cnvs = itertools.ifilter(
            lambda c: not any(
                'H2' in r.type or 'H3' in r.type for r in c._renderers),
            cnvs
        )
    cnvs = switch_log_scale(cnvs)
    cnvs = save(
        cnvs,
        lambda c: filename_func(c) + '_log'
    )
    return cnvs


if __name__ == '__main__':
    import doctest
    doctest.testmod()


# TODO use WrapperWrapper

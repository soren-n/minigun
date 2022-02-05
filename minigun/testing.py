# External module dependencies
from typing import (
    cast,
    ParamSpec,
    Any,
    Generic,
    List,
    Dict,
    Tuple,
    Callable
)
from dataclasses import dataclass
from inspect import signature
from functools import partial
from tqdm.auto import trange

# Internal module dependencies
from . import arbitrary as a
from . import quantify as q
from . import stream as s
from . import domain as d
from . import maybe as m

###############################################################################
# Test decorators
###############################################################################
P = ParamSpec('P')
Q = ParamSpec('Q')

Predicate = Callable[[Any], bool]
Law = Callable[P, bool]

@dataclass
class Unit(Generic[P]):
    law: Law[P]
    args: Dict[str, q.Sampler[Any]]
    refines: Dict[str, Predicate]

def domain(*lparams : q.Sampler[Any], **kparams : q.Sampler[Any]):
    def _decorate(law : Law[P]) -> Unit[P]:
        sig = signature(law)
        params = list(sig.parameters.keys())
        arg_types = {
            p.name : cast(type, p.annotation)
            for p in sig.parameters.values()
        }

        # Gather declared arguments
        args : Dict[str, q.Sampler[Any]] = {}
        for param, arg_sampler in zip(params[:len(lparams)], lparams):
            args[param] = arg_sampler
        for param, arg_sampler in kparams.items():
            args[param] = arg_sampler

        # Infer missing arguments
        for param in set(params).difference(set(args.keys())):
            arg_type = arg_types[param]
            arg_sampler = q.infer(arg_type)
            if isinstance(arg_sampler, m.Nothing):
                raise RuntimeError(
                    'Failed to infer domain for '
                    'parameter %s of functions %s' % (
                        param, law.__name__
                    )
                )
            args[param] = arg_sampler.value

        # Wrap up the unit
        return Unit(law, args, {})
    return _decorate

def _distribute_dict_domain(
    args : Dict[str, d.Domain[Any]]
    ) -> d.Domain[Dict[str, Any]]:
    def _thunk(params : List[str]) -> s.StreamResult[d.Domain[Dict[str, Any]]]:
        if len(params) == 0: raise StopIteration
        _params = params.copy()
        param = _params.pop(0)
        arg_stream = d.tail(args[param])
        if s.is_empty(arg_stream): return _thunk(_params)
        next_arg_domain, _ = arg_stream()
        _args = args.copy()
        _args[param] = next_arg_domain
        return _distribute_dict_domain(_args), partial(_thunk, _params)
    values = { param : d.head(arg) for param, arg in args.items() }
    return values, partial(_thunk, list(args.keys()))

def _trim_counter_example(
    law : Law[P],
    counter_example : Dict[str, d.Domain[Any]]
    ) -> Dict[str, Any]:

    def _is_counter_example(args : d.Domain[Dict[str, Any]]) -> bool:
        return not law(**d.head(args))

    def _search(args : d.Domain[Dict[str, Any]]) -> Dict[str, Any]:
        init_args, trimmed_args = args
        while True:
            _args = s.peek(s.filter(_is_counter_example, trimmed_args))
            if isinstance(_args, m.Something):
                init_args, trimmed_args = _args.value
                continue
            return init_args

    return _search(_distribute_dict_domain(counter_example))

def _find_counter_example(
    name : str,
    count : int,
    unit : Unit[P],
    state : a.State
    ) -> Tuple[a.State, m.Maybe[Dict[str, Any]]]:
    for _ in trange(count, desc = name):
        example : Dict[str, d.Domain[Any]] = {}
        for param, arg_sampler in unit.args.items():
            state, arg = arg_sampler(state)
            example[param] = arg
        _example = { param : d.head(arg) for param, arg in example.items() }
        if unit.law(**_example): continue
        return state, m.Something(_trim_counter_example(unit.law, example))
    return state, m.Nothing()

@dataclass
class Test(Generic[P]):
    name: str
    count: int
    unit: Callable[[a.State], Tuple[a.State, m.Maybe[Dict[str, Any]]]]

def test(name : str, count : int):
    def _decorate(unit : Unit[P]):
        _unit = partial(_find_counter_example, name, count, unit)
        return Test(name, count, _unit)
    return _decorate

###############################################################################
# Test runner
###############################################################################
class Suite:
    def __init__(self, *tests : Test[P]):
        self._tests = tests

    def evaluate(self, args : List[str]) -> bool:
        state = a.seed()
        for test in self._tests:
            state, counter_example = test.unit(state)
            if isinstance(counter_example, m.Nothing): continue
            print(
                'Test \"%s\" failed with the '
                'following counter example:' % test.name
            )
            print(counter_example.value)
            return False
        print('All tests passed!')
        return True
import argparse
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Dict, Tuple, Any, Callable, Union, List

import optuna
import yaml
from dataclasses_json import DataClassJsonMixin

from housing_model.training.generators import (
    FloatGenerator,
    DateGenerator,
    IntGenerator,
)
from housing_model.training.trainer import train_job, ExperimentSpec

suggester_factory = {
    "date_generator": DateGenerator,
    "float_generator": FloatGenerator,
    "int_generator": IntGenerator,
}


@dataclass
class VariableSpace(DataClassJsonMixin):
    name: str
    generator: str
    generator_config: Dict[str, Any]

    def make_generator(self):
        suggester_cls = suggester_factory[self.generator]
        return suggester_cls(self.name, **self.generator_config)


@dataclass
class HyperOptSpec(DataClassJsonMixin):
    variables: List[VariableSpace]
    name: str
    output: str
    template: Dict[str, Any]


@dataclass
class HyperParameterObjective:
    config: HyperOptSpec
    train_op: Callable[[ExperimentSpec, str], float]
    experience_path: Path

    def __post_init__(self):
        self._template_str = (
            yaml.dump(self.config.template).replace("'{", "{").replace("}'", "}")
        )
        self._variable_generator = {
            var.name: var.make_generator() for var in self.config.variables
        }

    def __call__(self, trial: optuna.Trial) -> float:
        name, exp_config = self._create_exp_config(trial)
        return self.train_op(exp_config, str(self.experience_path / name))

    def _create_exp_config(self, trial: optuna.Trial) -> Tuple[str, ExperimentSpec]:
        variables: Dict[str, Union[int, str]] = {}
        for name, generator in self._variable_generator.items():
            val = generator.generate(trial)
            if isinstance(val, str):
                variables[name] = f"'{val}'"
            elif isinstance(val, (int, float)):
                variables[name] = val
            else:
                raise ValueError(f"Please convert {type(val)} to compatible json types")

        config_yaml = self._template_str
        try:
            config_yaml = config_yaml.format(**variables)
            config_dict = yaml.safe_load(config_yaml)
        except KeyError as e:
            raise ValueError(f"cannot format with {str(variables)}") from e

        exp_config, trial_name = self.make_uniqu_name(config_dict, variables)
        return trial_name, exp_config

    def make_uniqu_name(self, config_dict, variables):
        exp_config = ExperimentSpec.from_dict(config_dict)
        custom_name = self.config.name.format(**variables).replace("'", "")  # Remove
        timestamp = (datetime.now() - datetime(2000, 1, 1)).total_seconds()
        trial_name = f"{custom_name}_{int(timestamp)}"
        return exp_config, trial_name


def hyper_parameters_tuning(config: HyperOptSpec, output: str, n_trials: int):
    study_name = config.output  # Unique identifier of the study.

    output_path = Path(output) / study_name
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "hyper-params-config.json", "w") as config_file:
        config_file.write(config.to_json(indent=2))

    storage_name = f"sqlite:////{os.path.abspath(output_path)}/{study_name}.db"
    study = optuna.create_study(
        study_name=study_name, storage=storage_name, load_if_exists=True
    )

    study.optimize(
        HyperParameterObjective(config, train_job, output_path), n_trials=n_trials
    )


def main(exp_template: str, output: str, n_trials: int):
    with open(exp_template) as exp_file:
        config = HyperOptSpec.from_json(exp_file.read())

    hyper_parameters_tuning(config, output, n_trials)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp-template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--n-trials", type=int, required=True)

    args = parser.parse_args()
    main(**vars(args))

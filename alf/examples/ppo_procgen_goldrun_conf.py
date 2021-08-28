# Copyright (c) 2021 Horizon Robotics and ALF Contributors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import alf
from alf.examples import ppo_conf
from alf.examples import procgen_conf
from alf.examples.networks import impala_cnn_encoder
from alf.algorithms.data_transformer import RewardScaling
from alf.utils.losses import element_wise_huber_loss

# Environment Configuration
alf.config(
    'create_environment',
    env_name='procgen:procgen-coinrun-v0',
    num_parallel_environments=64)

# Construct the networks


def policy_network_ctor(input_tensor_spec, action_spec):
    encoder_output_size = 256
    return alf.nn.Sequential(
        impala_cnn_encoder.create(
            input_tensor_spec=input_tensor_spec,
            cnn_channel_list=(16, 32, 32),
            num_blocks_per_stack=2,
            output_size=encoder_output_size),
        alf.networks.CategoricalProjectionNetwork(
            input_size=encoder_output_size, action_spec=action_spec))


def value_network_ctor(input_tensor_spec):
    encoder_output_size = 256
    return alf.nn.Sequential(
        impala_cnn_encoder.create(
            input_tensor_spec=input_tensor_spec,
            cnn_channel_list=(16, 32, 32),
            num_blocks_per_stack=2,
            output_size=encoder_output_size),
        alf.layers.FC(input_size=encoder_output_size, output_size=1),
        alf.layers.Reshape(shape=()))


# Construct the algorithm

alf.config(
    'ActorCriticAlgorithm',
    actor_network_ctor=policy_network_ctor,
    value_network_ctor=value_network_ctor,
    optimizer=alf.optimizers.AdamTF(lr=1e-3))

# Turn off enforce_entropy_target. It is turned on by default in
# ppo_conf. Turning this on may have negative impact
alf.config('Agent', enforce_entropy_target=False)

alf.config(
    'PPOLoss',
    entropy_regularization=1e-4,
    gamma=0.98,
    td_error_loss_fn=element_wise_huber_loss,
    normalize_advantages=False)

# training config
alf.config(
    'TrainerConfig',
    unroll_length=256,
    mini_batch_length=1,
    mini_batch_size=128,
    num_updates_per_train_iter=4,
    num_iterations=1000,
    num_checkpoints=5,
    evaluate=True,
    eval_interval=50,
    debug_summaries=False,
    summary_interval=10)

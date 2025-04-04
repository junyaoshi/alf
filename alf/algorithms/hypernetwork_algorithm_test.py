# Copyright (c) 2020 Horizon Robotics and ALF Contributors. All Rights Reserved.
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
"""Hypernetwork algorithm test."""

import absl
from absl.testing import parameterized
import numpy as np
import torch
import torch.nn.functional as F

import alf
from alf.algorithms.hypernetwork_algorithm import HyperNetwork
from alf.tensor_specs import TensorSpec
from alf.utils import math_ops
from alf.utils.datagen import TestDataSet
from torch.utils.data import TensorDataset, DataLoader


class HyperNetworkTest(parameterized.TestCase, alf.test.TestCase):
    def cov(self, data, rowvar=False):
        """Estimate a covariance matrix given data.

        Args:
            data (tensor): A 1-D or 2-D tensor containing multiple observations
                of multiple dimentions. Each row of ``mat`` represents a
                dimension of the observation, and each column a single
                observation.
            rowvar (bool): If True, then each row represents a dimension, with
                observations in the columns. Othewise, each column represents
                a dimension while the rows contains observations.

        Returns:
            The covariance matrix
        """
        x = data.detach().clone()
        if x.dim() > 2:
            raise ValueError('data has more than 2 dimensions')
        if x.dim() < 2:
            x = x.view(1, -1)
        if not rowvar and x.size(0) != 1:
            x = x.t()
        fact = 1.0 / (x.size(1) - 1)
        x -= torch.mean(x, dim=1, keepdim=True)
        return fact * x.matmul(x.t()).squeeze()

    def assertArrayGreater(self, x, y, eps):
        self.assertEqual(x.shape, y.shape)
        self.assertGreater(float(torch.min(x - y)), eps)

    @parameterized.parameters(('gfsf', False), ('svgd2', False),
                              ('svgd3', False), ('minmax', False),
                              ('gfsf', True), ('svgd2', True), ('svgd3', True),
                              ('svgd', False, True))
    def test_bayesian_linear_regression(self,
                                        par_vi='svgd3',
                                        function_vi=False,
                                        functional_gradient=False,
                                        train_batch_size=10,
                                        num_particles=128):
        r"""
        The hypernetwork is trained to generate the parameter vector for a linear
        regressor. The target linear regressor is :math:`y = X\beta + e`, where
        :math:`e\sim N(0, I)` is random noise, :math:`X` is the input data matrix,
        and :math:`y` is target ouputs. The posterior of :math:`\beta` has a
        closed-form :math:`p(\beta|X,y)\sim N((X^TX)^{-1}X^Ty, X^TX)`.
        For a linear generator with weight W and bias b, and takes standard Gaussian
        noise as input, the output follows a Gaussian :math:`N(b, WW^T)`, which should
        match the posterior :math:`p(\beta|X,y)` for both ``svgd``, ``gfsf``, and
        ``minmax``.

        """
        input_size = 3
        input_spec = TensorSpec((input_size, ), torch.float32)
        output_dim = 1
        batch_size = 50
        hidden_size = output_dim * batch_size
        inputs = input_spec.randn(outer_dims=(batch_size, ))
        beta = torch.rand(input_size, output_dim) + 5.
        print("beta: {}".format(beta))
        noise = torch.randn(batch_size, output_dim)
        targets = inputs @ beta + noise
        true_cov = torch.inverse(inputs.t() @ inputs)
        true_mean = true_cov @ inputs.t() @ targets
        noise_dim = 3

        if functional_gradient:
            hidden_layers = ()
        else:
            hidden_layers = None

        algorithm = HyperNetwork(
            input_tensor_spec=input_spec,
            output_dim=output_dim,
            use_bias_for_last_layer=False,
            last_activation=math_ops.identity,
            noise_dim=noise_dim,
            hidden_layers=hidden_layers,
            loss_type='regression',
            par_vi=par_vi,
            function_vi=function_vi,
            functional_gradient=functional_gradient,
            critic_hidden_layers=(hidden_size, hidden_size),
            inverse_mvp_hidden_layers=3,
            function_bs=train_batch_size,
            optimizer=alf.optimizers.Adam(lr=2e-3),
            inverse_mvp_optimizer=alf.optimizers.Adam(lr=1e-4),
            critic_optimizer=alf.optimizers.Adam(lr=1e-3))
        print("ground truth mean: {}".format(true_mean))
        print("ground truth cov: {}".format(true_cov))
        print("ground truth cov norm: {}".format(true_cov.norm()))

        def _train(train_batch=None, entropy_regularization=None):
            if train_batch is None:
                perm = torch.randperm(batch_size)
                idx = perm[:train_batch_size]
                train_inputs = inputs[idx]
                train_targets = targets[idx]
            else:
                train_inputs, train_targets = train_batch
            if entropy_regularization is None:
                entropy_regularization = train_batch_size / batch_size
            alg_step = algorithm.train_step(
                inputs=(train_inputs, train_targets),
                entropy_regularization=entropy_regularization,
                num_particles=num_particles)

            loss_info, params = algorithm.update_with_gradient(alg_step.info)

        def _test(i, sampled_predictive=False):
            print("-" * 68)
            weight = algorithm._generator._net[0].weight
            learned_cov = weight @ weight.t()
            print("norm of generator weight: {}".format(weight.norm()))
            print("norm of learned_cov: {}".format(learned_cov.norm()))

            learned_mean = algorithm._generator._net[0].bias
            predicts = inputs @ learned_mean  # [batch]
            pred_err = torch.norm(predicts - targets.squeeze())
            print("train_iter {}: pred err {}".format(i, pred_err))

            mean_err = torch.norm(learned_mean - true_mean.squeeze())
            mean_err = mean_err / torch.norm(true_mean)
            print("train_iter {}: mean err {}".format(i, mean_err))

            cov_err = torch.norm(learned_cov - true_cov)
            cov_err = cov_err / torch.norm(true_cov)
            print("train_iter {}: cov err {}".format(i, cov_err))

            if sampled_predictive:
                params = algorithm.sample_parameters(num_particles=200)
                pred_step = algorithm.predict_step(inputs, params=params)
                sampled_preds = pred_step.output.squeeze(
                )  # [batch, n_particles]
                spred_err = torch.norm((sampled_preds - targets).mean(1))
                print("train_iter {}: sampled pred err {}".format(
                    i, spred_err))

                computed_mean = params.mean(0)
                smean_err = torch.norm(computed_mean - true_mean.squeeze())
                smean_err = smean_err / torch.norm(true_mean)
                print("train_iter {}: sampled mean err {}".format(
                    i, smean_err))

                computed_cov = self.cov(params)
                scov_err = torch.norm(computed_cov - true_cov)
                scov_err = scov_err / torch.norm(true_cov)
                print("train_iter {}: sampled cov err {}".format(i, scov_err))

        train_iter = 3000
        for i in range(train_iter):
            _train()
            if i % 1000 == 0:
                _test(i)

        learned_mean = algorithm._generator._net[0].bias
        mean_err = torch.norm(learned_mean - true_mean.squeeze())
        mean_err = mean_err / torch.norm(true_mean)
        weight = algorithm._generator._net[0].weight
        learned_cov = weight @ weight.t()
        cov_err = torch.norm(learned_cov - true_cov)
        cov_err = cov_err / torch.norm(true_cov)
        print("-" * 68)
        print("train_iter {}: mean err {}".format(train_iter, mean_err))
        print("train_iter {}: cov err {}".format(train_iter, cov_err))

        self.assertLess(mean_err, 0.5)
        self.assertLess(cov_err, 0.5)

    def test_hypernetwork_uncertainty(self,
                                      par_vi='svgd3',
                                      function_vi=False,
                                      train_batch_size=10,
                                      num_particles=10):

        input_spec = TensorSpec((1, 28, 28), torch.float32)
        noise_dim = 128

        trainset = TensorDataset(
            torch.randn(100, 1, 28, 28), torch.randint(0, 9, (100, )))
        testset = TensorDataset(
            torch.randn(50, 1, 28, 28), torch.randint(0, 9, (50, )))
        outlier_trainset = TensorDataset(
            torch.randn(100, 1, 28, 28), torch.randint(0, 9, (100, )))
        outlier_testset = TensorDataset(
            torch.randn(50, 1, 28, 28), torch.randint(0, 9, (50, )))

        trainset.classes = torch.arange(10)
        testset.classes = torch.arange(10)
        outlier_trainset.classes = torch.arange(10)
        outlier_testset.classes = torch.arange(10)

        train_loader = DataLoader(trainset, train_batch_size)
        test_loader = DataLoader(testset, train_batch_size)
        outlier_train_loader = DataLoader(trainset, train_batch_size)
        outlier_test_loader = DataLoader(trainset, train_batch_size)

        conv_layer_params = ((6, 5, 1, 2, 2), (16, 5, 1, 0, 2), (120, 5, 1))
        fc_layer_params = ((84, True), )
        hidden_layers = (noise_dim, 256)
        algorithm = HyperNetwork(
            input_tensor_spec=input_spec,
            output_dim=10,
            conv_layer_params=conv_layer_params,
            fc_layer_params=fc_layer_params,
            hidden_layers=hidden_layers,
            num_particles=num_particles,
            last_activation=math_ops.identity,
            noise_dim=noise_dim,
            loss_type='classification',
            par_vi=par_vi,
            function_vi=function_vi,
            function_bs=train_batch_size,
            optimizer=alf.optimizers.Adam(lr=1e-3))

        algorithm.set_data_loader(
            train_loader,
            test_loader=test_loader,
            outlier_data_loaders=(outlier_train_loader, outlier_test_loader),
            entropy_regularization=train_batch_size / 100)

        def _test(sampled_predictive=False):
            print("-" * 68)
            algorithm.evaluate(num_particles=num_particles)
            return algorithm.eval_uncertainty(num_particles=num_particles)

        algorithm.train_iter()
        auc_entropy, auc_var = _test()
        self.assertGreater(auc_entropy, 0.49)
        self.assertGreater(auc_var, 0.49)


if __name__ == "__main__":
    alf.test.main()

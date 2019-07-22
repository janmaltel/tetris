import numpy as np

import stew
from stew.utils import create_ridge_matrix, create_diff_matrix, create_stnw_matrix
import domtools

from tetris import mcts, tetromino, game
from tetris.utils import plot_analysis, plot_individual_agent, vert_one_hot
from numba import njit
from scipy.stats import binom_test
from sklearn.linear_model import LinearRegression
from statsmodels.stats.proportion import proportions_ztest
import gc

import cma

import time


class Cbmpi:
    def __init__(self, m, N, M, B, D, feature_type, num_columns, cmaes_var, verbose, seed=0, id="",
                 discrete_choice=False):
        self.num_features = 8
        self.num_value_features = 13
        self.num_columns = num_columns
        self.feature_type = feature_type
        self.verbose = verbose
        self.max_choice_set_size = 35
        self.tetrominos = [tetromino.Straight(self.feature_type, self.num_features, self.num_columns),
                           tetromino.RCorner(self.feature_type, self.num_features, self.num_columns),
                           tetromino.LCorner(self.feature_type, self.num_features, self.num_columns),
                           tetromino.Square(self.feature_type, self.num_features, self.num_columns),
                           tetromino.SnakeR(self.feature_type, self.num_features, self.num_columns),
                           tetromino.SnakeL(self.feature_type, self.num_features, self.num_columns),
                           tetromino.T(self.feature_type, self.num_features, self.num_columns)]
        self.tetromino_sampler = tetromino.TetrominoSampler(self.tetrominos)

        self.m = m  # rollout_size
        self.M = M  # number of independent rollouts per
        assert (self.M == 1)
        self.B = B  # budget
        if N is None:
            self.N = int(self.B / self.M / (self.m+1) / 32)
            print("Given the budget of", self.B, " the rollout set size will be:", self.N)
        else:
            self.N = N  # number of states sampled from rollout_set D_k
        self.D = D  # rollout set
        self.D_k = None
        # # TODO: put in learn??
        # self.construct_rollout_set()

        # print("The budget is", self.B)
        self.gamma = 1
        self.eta = 0  # CMA-ES parameter (dunno where to set...)
        self.zeta = 0.5  # CMA-ES parameter ( // 2 is standard in cma)
        self.n = int(self.num_features * 15)  # CMA-ES parameter ('popsize' in cma)
        self.cmaes_var = cmaes_var

        self.policy_weights = np.random.normal(loc=0, scale=self.cmaes_var, size=self.num_features)
        self.value_weights = np.zeros(self.num_value_features)

        self.lin_reg = LinearRegression(n_jobs=1, fit_intercept=True)  # n_jobs must be 1... otherwise clashes with multiprocessing.Pool
        self.lin_reg.coef_ = self.value_weights
        self.lin_reg.intercept_ = 0.

        self.seed = seed

        self.discrete_choice = discrete_choice
        if self.discrete_choice:
            self.ols = True
            self.model = stew.StewMultinomialLogit(num_features=self.num_features)
            self.mlogit_data = MlogitData(num_features=self.num_features, max_choice_set_size=self.max_choice_set_size)
        else:
            self.cma_es = cma.CMAEvolutionStrategy(np.random.normal(loc=0, scale=1, size=self.num_features), self.cmaes_var, inopts={'verb_disp': 0,
                                                                                   'verb_filenameprefix': "cmaesout" + str(id) + "_" + str(self.seed),
                                                                                   'popsize': self.n})

    def learn(self):
        self.construct_rollout_set()
        if self.verbose:
            print("Rollout set constructed")
        start_time = time.time()
        self.state_features = np.zeros((self.N, self.num_value_features), dtype=np.float)
        self.state_values = np.zeros(self.N, dtype=np.float)
        self.state_action_values = np.zeros((self.N, 34), dtype=np.float)
        # self.state_action_features = []
        self.state_action_features = np.zeros((self.N, 34, self.num_features))
        self.num_available_actions = np.zeros(self.N, dtype=int)
        self.did_rollout = np.ones(self.N, dtype=bool)
        for ix, rollout_state in enumerate(self.D_k):
            # if ix % 5000 == 0 and self.verbose:
            #     print("rollout state:", ix)
            self.state_features[ix, :] = rollout_state.get_features(direct_by=None, order_by=None, standardize_by=None, addRBF=True)
            self.state_values[ix] = self.value_roll_out(rollout_state)
            actions_value_estimates, state_action_features = self.action_value_roll_out(rollout_state)
            num_available_actions = len(actions_value_estimates)
            self.num_available_actions[ix] = num_available_actions
            self.state_action_values[ix, :num_available_actions] = actions_value_estimates
            if state_action_features is not None:
                self.state_action_features[ix, :num_available_actions, :] = state_action_features
            else:
                self.did_rollout[ix] = False
            # self.state_action_features.append(state_action_features)

        self.delete_rollout_set()
        end_time = time.time()
        if self.verbose:
            print("Rollouts took " + str((end_time - start_time) / 60) + " minutes.")
        assert(len(self.state_action_features) == self.N)
        # # Max
        # self.max_state_action_values = self.state_action_values.max(axis=1)

        if self.verbose:
            print("Estimating weights now...")

        # Approximate value function
        if self.verbose:
            print("Number of self.state_values", len(self.state_values))
        self.lin_reg.fit(self.state_features, self.state_values)
        self.value_weights = np.hstack((self.lin_reg.intercept_, self.lin_reg.coef_))
        if self.verbose:
            print("new value weights: ", self.value_weights)

        # Approximate policy
        start_time = time.time()
        if self.discrete_choice:
            self.mlogit_data.delete_data()
            # stacked_features = np.concatenate([self.state_action_features, axis=0)
            # TODO: rewrite to use less copying / memory...
            for state_ix in range(self.N):
                state_act_feat_ix = self.state_action_features[state_ix]
                if state_act_feat_ix is not None:
                    action_values = self.state_action_values[state_ix, :len(state_act_feat_ix)]
                    self.mlogit_data.push(features=self.state_action_features[state_ix], choice_index=np.argmax(action_values), delete_oldest=False)
            if self.ols:
                self.policy_weights = self.model.fit(data=self.mlogit_data.sample(), lam=0, standardize=False)
            # else:
            #     self.policy_weights, _ = self.model.cv_fit(data=self.mlogit_data.sample(), standardize=self.standardize_features)
        else:
            self.cma_es = cma.CMAEvolutionStrategy(np.random.normal(loc=0, scale=1, size=self.num_features), self.cmaes_var,
                                                   inopts={'verb_disp': 0,
                                                           'verb_filenameprefix': "cmaesout" + str(id) + "_" + str(self.seed),
                                                           'popsize': self.n})
            self.policy_weights = self.cma_es.optimize(self.policy_loss_function, min_iterations=1).result.xbest
        end_time = time.time()
        if self.verbose:
            print("CMAES took " + str((end_time - start_time) / 60) + " minutes.")
            print("new policy_weights: ", self.policy_weights)

    def construct_rollout_set(self):
        self.D_k = np.random.choice(a=self.D, size=self.N, replace=False)

    def delete_rollout_set(self):
        self.D_k = None
        gc.collect()

    def policy_loss_function(self, pol_weights):
        loss = 0.
        number_of_samples = 0
        for state_ix in range(self.N):
            # print("self.N", self.N)
            if self.did_rollout[state_ix]:
                values = self.state_action_features[state_ix, :self.num_available_actions[state_ix]].dot(pol_weights)
                # print("values", values)
                pol_value = self.state_action_values[state_ix, np.argmax(values)]
                max_value = np.max(self.state_action_values[state_ix, :len(values)])
                # print("state_ix", state_ix)
                # print("self.state_action_features[state_ix]", self.state_action_features[state_ix])
                # print("pol_weights", pol_weights)
                loss += max_value - pol_value
                number_of_samples += 1
            # else:
            #     pass
            #     # print(state_ix, " has no action features / did not produce a rollout!")
        loss /= number_of_samples
        # print("loss", loss)
        return loss

    def value_roll_out(self, start_state):
        # value_estimate = start_state.reward
        value_estimate = 0.0
        state_tmp = start_state
        count = 0
        game_ended = False
        # print("------------------------------------")
        # print("------------------------------------")
        # print("Starting new value rollout")
        # print("------------------------------------")
        # print("------------------------------------")
        while not game_ended and count < self.m:  # there are only (m-1) rollouts
            tetromino_tmp = self.tetromino_sampler.next_tetromino()
            # print("state_tmp.representation")
            # print(state_tmp.representation)
            state_tmp, _ = self.choose_action_value_rollout(start_state=state_tmp, start_tetromino=tetromino_tmp)
            if state_tmp is None:
                game_ended = True
            else:
                value_estimate += (self.gamma ** count) * state_tmp.reward
            count += 1

        # One more (the m-th) for truncation value!
        if not game_ended:
            tetromino_tmp = self.tetromino_sampler.next_tetromino()
            state_tmp, _ = self.choose_action_value_rollout(start_state=state_tmp, start_tetromino=tetromino_tmp)
            if state_tmp is not None:
                final_state_features = state_tmp.get_features(direct_by=None, order_by=None, standardize_by=None, addRBF=True)
                # value_estimate += self.gamma ** count + final_state_features.dot(self.value_weights)
                value_estimate += (self.gamma ** count) * self.lin_reg.predict(final_state_features.reshape(1, -1))
        return value_estimate

    def action_value_roll_out(self, start_state):
        start_tetromino = self.tetromino_sampler.next_tetromino()
        all_children_states = start_tetromino.get_after_states(current_state=start_state)
        children_states = np.array([child for child in all_children_states if not child.terminal_state])
        num_children = len(children_states)
        action_value_estimates = np.zeros(num_children)
        state_action_features = np.zeros((num_children, self.num_features))
        if num_children == 0:
            # TODO: check whether this (returning zeros) has any side effects on learning...
            # Use the None (instead of state_action_features) to propagate the knowledge that there was no decision to make here.
            return action_value_estimates, None
        for child_ix in range(num_children):
            state_tmp = children_states[child_ix]
            state_action_features[child_ix] = state_tmp.get_features(direct_by=None, order_by=None, standardize_by=None)
            cumulative_reward = state_tmp.reward
            # cumulative_reward = 0

            # print("------------------------------------")
            # print("------------------------------------")
            # print("Starting new action rollout")
            # print("------------------------------------")
            # print("------------------------------------")
            game_ended = False
            count = 0
            while not game_ended and count < self.m:  # there are m rollouts
                tetromino_tmp = self.tetromino_sampler.next_tetromino()
                # print("state_tmp.representation")
                # print(state_tmp.representation)
                state_tmp, _ = self.choose_action_q_rollout(start_state=state_tmp, start_tetromino=tetromino_tmp)
                if state_tmp is None:
                    game_ended = True
                else:
                    cumulative_reward += (self.gamma ** count) * state_tmp.reward
                count += 1

            # One more (the (m+1)-th) for truncation value!
            if not game_ended:
                tetromino_tmp = self.tetromino_sampler.next_tetromino()
                state_tmp, _ = self.choose_action_q_rollout(start_state=state_tmp, start_tetromino=tetromino_tmp)
                if state_tmp is not None:
                    final_state_features = state_tmp.get_features(direct_by=None, order_by=None, standardize_by=None, addRBF=True)
                    # cumulative_reward += self.gamma ** count + final_state_features.dot(self.value_weights)
                    cumulative_reward += (self.gamma ** count) * self.lin_reg.predict(final_state_features.reshape(1, -1))

            action_value_estimates[child_ix] = cumulative_reward
        return action_value_estimates, state_action_features

    def choose_action_value_rollout(self, start_state, start_tetromino):
        return self.choose_action_q_rollout(start_state, start_tetromino)

    def choose_action_q_rollout(self, start_state, start_tetromino):
        # Returning "None" if game is over.
        all_available_after_states = start_tetromino.get_after_states(current_state=start_state)
        available_after_states = np.array([child for child in all_available_after_states if not child.terminal_state])
        num_states = len(available_after_states)
        if num_states == 0:
            # Game over!
            return None, 0
        action_features = np.zeros((num_states, self.num_features))
        for ix, after_state in enumerate(available_after_states):
            action_features[ix] = after_state.get_features(direct_by=None, order_by=None, standardize_by=None)
        utilities = action_features.dot(self.policy_weights)
        max_indices = np.where(utilities == np.max(utilities))[0]
        move_index = np.random.choice(max_indices)
        move = available_after_states[move_index]
        return move, move_index

    def choose_action_test(self, start_state, start_tetromino):
        # Returning the first state if game is over.
        all_available_after_states = start_tetromino.get_after_states(current_state=start_state)
        available_after_states = np.array([child for child in all_available_after_states if not child.terminal_state])
        num_states = len(available_after_states)
        if num_states == 0:
            # Game over!
            return all_available_after_states[0], 0
        action_features = np.zeros((num_states, self.num_features))
        for ix, after_state in enumerate(available_after_states):
            action_features[ix] = after_state.get_features(direct_by=None, order_by=None, standardize_by=None)
        utilities = action_features.dot(self.policy_weights)
        max_indices = np.where(utilities == np.max(utilities))[0]
        move_index = np.random.choice(max_indices)
        move = available_after_states[move_index]
        return move, move_index

    def choose_action_test_hard(self, start_state, start_tetromino):
        # Returning the first state if game is over.
        available_after_states = start_tetromino.get_after_states(current_state=start_state)
        num_states = len(available_after_states)
        action_features = np.zeros((num_states, self.num_features))
        for ix, after_state in enumerate(available_after_states):
            action_features[ix] = after_state.get_features(direct_by=None, order_by=None, standardize_by=None)
        utilities = action_features.dot(self.policy_weights)
        max_indices = np.where(utilities == np.max(utilities))[0]
        move_index = np.random.choice(max_indices)
        move = available_after_states[move_index]
        return move, move_index
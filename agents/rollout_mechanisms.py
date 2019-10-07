import numpy as np
from stew import StewMultinomialLogit, ChoiceSetData
from tetris import tetromino
from numba import njit
from sklearn.linear_model import LinearRegression
import gc
import cma
import time

class OnlineRollout:
    def __init__(self,
                 rollout_length,
                 rollouts_per_action,
                 num_features,
                 num_value_features,
                 rollout_set_size=None,
                 gamma=1):
        self.name = "OnlineRollout"
        self.rollout_length = rollout_length
        self.rollouts_per_action = rollouts_per_action
        self.rollout_set_size = rollout_set_size
        self.budget = self.rollout_length * self.rollouts_per_action * self.rollout_set_size * 32
        self.gamma = gamma


class BatchRollout:
    def __init__(self,
                 rollout_state_population,
                 rollout_length,
                 rollouts_per_action,
                 rollout_set_size,
                 num_features,
                 num_value_features,
                 gamma=1):
        self.name = "BatchRollout"
        self.rollout_state_population = rollout_state_population
        self.rollout_set = None  # use self.construct_rollout_set()
        self.rollout_length = rollout_length
        self.rollouts_per_action = rollouts_per_action
        self.rollout_set_size = rollout_set_size
        print("The budget is: ", self.rollout_set_size * self.rollouts_per_action * (self.rollout_length+1) * 32)
        self.gamma = gamma
        self.num_features = num_features
        self.num_value_features = num_value_features

    def construct_rollout_set(self):
        self.rollout_set = np.random.choice(a=self.rollout_state_population, size=self.rollout_set_size, replace=False)

    def perform_rollouts(self, policy_weights, value_weights, generative_model, use_state_values):
        self.construct_rollout_set()
        state_features = np.zeros((self.rollout_set_size, self.num_value_features), dtype=np.float)
        state_values = np.zeros(self.rollout_set_size, dtype=np.float)
        state_action_values = np.zeros((self.rollout_set_size, 34), dtype=np.float)
        state_action_features = np.zeros((self.rollout_set_size, 34, self.num_features))
        num_available_actions = np.zeros(self.rollout_set_size, dtype=np.int64)
        did_rollout = np.ones(self.rollout_set_size, dtype=bool)
        for ix, rollout_state in enumerate(self.rollout_set):
            # Sample tetromino for each rollout state (same for state and state-action rollouts)
            generative_model.next_tetromino()

            if use_state_values:
                # Rollouts for state-value function estimation
                state_features[ix, :] = rollout_state.get_features_pure(True)[1:]  # Don't store intercept
                state_values[ix] = value_roll_out(rollout_state, self.rollout_length, self.gamma,
                                                  generative_model.copy_with_same_current_tetromino(),
                                                  policy_weights, value_weights, self.num_features)

            # Rollouts for action-value function estimation
            actions_value_estimates, state_action_features_ix = \
                action_value_roll_out(rollout_state, self.rollout_length, self.rollouts_per_action, self.gamma,
                                      generative_model.copy_with_same_current_tetromino(),
                                      policy_weights, value_weights, self.num_features, use_state_values)
            num_av_acts = len(actions_value_estimates)
            num_available_actions[ix] = num_av_acts
            state_action_values[ix, :num_av_acts] = actions_value_estimates
            if num_av_acts > 0:
                state_action_features[ix, :num_av_acts, :] = state_action_features_ix
            else:
                did_rollout[ix] = False
        # return state_features, state_values, state_action_features, state_action_values, did_rollout, num_available_actions
        return dict(state_features=state_features,
                    state_values=state_values,
                    state_action_features=state_action_features,
                    state_action_values=state_action_values,
                    did_rollout=did_rollout,
                    num_available_actions=num_available_actions)


@njit(cache=False)
def action_value_roll_out(start_state,
                          rollout_length,
                          rollouts_per_action,
                          gamma,
                          generative_model,
                          policy_weights,
                          value_weights,
                          num_features,
                          use_state_values):
    # generative_model.next_tetromino()
    child_states = generative_model.get_after_states(start_state)
    num_child_states = len(child_states)
    action_value_estimates = np.zeros(num_child_states)
    state_action_features = np.zeros((num_child_states, num_features))
    if num_child_states == 0:
        # TODO: check whether this (returning zeros) has any side effects on learning...
        return action_value_estimates, state_action_features
    for child_ix in range(num_child_states):
        state_tmp = child_states[child_ix]
        start_reward = state_tmp.n_cleared_lines
        state_action_features[child_ix] = state_tmp.get_features_pure(False)  # order_by=None, standardize_by=None
        for rollout_ix in range(rollouts_per_action):
            cumulative_reward = start_reward
            # print("Starting new action rollout")
            game_ended = False
            count = 0
            while not game_ended and count < rollout_length:  # there are rollout_length rollouts
                generative_model.next_tetromino()
                available_after_states = generative_model.get_after_states(state_tmp)
                num_after_states = len(available_after_states)
                if num_after_states == 0:
                    game_ended = True
                else:
                    state_tmp = choose_action_in_rollout(available_after_states, policy_weights, num_features)
                    cumulative_reward += (gamma ** count) * state_tmp.n_cleared_lines
                count += 1

            # One more (the (rollout_length+1)-th) for truncation value!
            if use_state_values and not game_ended:
                generative_model.next_tetromino()
                available_after_states = generative_model.get_after_states(state_tmp)
                num_after_states = len(available_after_states)
                if num_after_states > 0:
                    state_tmp = choose_action_in_rollout(available_after_states, policy_weights, num_features)
                    final_state_features = state_tmp.get_features_pure(True)
                    cumulative_reward += (gamma ** count) * final_state_features.dot(value_weights)

            action_value_estimates[child_ix] += cumulative_reward
    action_value_estimates /= rollouts_per_action
    return action_value_estimates, state_action_features


@njit(cache=False)
def value_roll_out(start_state,
                   m,
                   gamma,
                   generative_model,
                   policy_weights,
                   value_weights,
                   num_features):
    value_estimate = 0.0
    state_tmp = start_state
    count = 0
    while not state_tmp.terminal_state and count < m:  # there are only (m-1) rollouts
        # generative_model.next_tetromino()
        available_after_states = generative_model.get_after_states(state_tmp)
        if len(available_after_states) == 0:
            return value_estimate
        state_tmp = choose_action_in_rollout(available_after_states, policy_weights,
                                             # rollout_dom_filter, rollout_cumu_dom_filter,
                                             num_features)
        value_estimate += gamma ** count * state_tmp.n_cleared_lines
        count += 1
        generative_model.next_tetromino()

    # One more (the m-th) for truncation value!
    if not state_tmp.terminal_state:
        # generative_model.next_tetromino()
        available_after_states = generative_model.get_after_states(state_tmp)
        if len(available_after_states) == 0:
            return value_estimate
        state_tmp = choose_action_in_rollout(available_after_states, policy_weights,
                                             # rollout_dom_filter, rollout_cumu_dom_filter,
                                             num_features)
        final_state_features = state_tmp.get_features_pure(True)  # order_by=None, standardize_by=None,
        # value_estimate += self.gamma ** count + final_state_features.dot(self.value_weights)
        value_estimate += (gamma ** count) * final_state_features.dot(value_weights)
    return value_estimate


@njit(cache=False)
def choose_action_in_rollout(available_after_states, policy_weights,
                             # rollout_dom_filter, rollout_cumu_dom_filter,
                             # feature_directors,
                             num_features):
    num_states = len(available_after_states)
    action_features = np.zeros((num_states, num_features))
    for ix, after_state in enumerate(available_after_states):
        action_features[ix] = after_state.get_features_pure(False)  # , order_by=self.feature_order
    # if rollout_cumu_dom_filter:
    #     not_simply_dominated, not_cumu_dominated = dom_filter(action_features, len_after_states=num_states)  # domtools.
    #     action_features = action_features[not_cumu_dominated]
    #     map_back_vector = np.nonzero(not_cumu_dominated)[0]
    #     # if rollout_cumu_dom_filter:
    #     #     available_after_states = available_after_states[not_simply_dominated]
    #     #     action_features = action_features[not_simply_dominated]
    #     # elif rollout_dom_filter:
    #     #     available_after_states = available_after_states[not_cumu_dominated]
    #     #     action_features = action_features[not_cumu_dominated]
    # else:
    #     raise ValueError("Currently only implemented with cumu_dom_filter")
    utilities = action_features.dot(np.ascontiguousarray(policy_weights))
    move_index = np.argmax(utilities)
    move = available_after_states[move_index]
    return move
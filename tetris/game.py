import numpy as np
from tetris import state, tetromino
import pprint
import collections
import time
import copy
# feature_names = ["holes", "cumulative_wells", "cumulative_wells_squared",
#                  "landing_height", "avg_free_row", "avg_free_row_squared", "n_landing_positions"]

from IPython.display import clear_output
import time


class Tetris:
    """
    Tetris for reinforcement learning applications.

    Tailored to use with a set of hand-crafted features such as "BCTS" (Thiery & Scherrer 2009)

    The BCTS feature names (and order) are
    ['rows_with_holes', 'column_transitions', 'holes', 'landing_height',
    'cumulative_wells', 'row_transitions', 'eroded', 'hole_depth']

    """
    def __init__(self,
                 num_columns,
                 num_rows,
                 verbose=False,
                 tetromino_size=4,
                 feature_type="bcts",
                 num_features=8,
                 max_cleared_test_lines=np.inf):
        """
        
        :param num_columns: 
        :param num_rows: 
        :param agent: 
        :param verbose: 
        :param tetromino_size:
        :param max_cleared_test_lines:
        """
        self.num_columns = num_columns
        self.num_rows = num_rows
        self.tetromino_size = tetromino_size
        # self.agent = agent
        self.verbose = verbose
        # self.target_update = target_update
        self.num_features = num_features
        self.feature_type = feature_type
        self.num_fields = self.num_columns * self.num_rows
        self.game_over = False
        self.current_state = state.State(representation=np.zeros((self.num_rows + self.tetromino_size, self.num_columns), dtype=np.int64),
                                         lowest_free_rows=np.zeros(self.num_columns, dtype=np.int64),
                                         num_features=self.num_features,
                                         feature_type="bcts")
        self.tetrominos = [tetromino.Straight(self.feature_type, self.num_features, self.num_columns),
                           tetromino.RCorner(self.feature_type, self.num_features, self.num_columns),
                           tetromino.LCorner(self.feature_type, self.num_features, self.num_columns),
                           tetromino.Square(self.feature_type, self.num_features, self.num_columns),
                           tetromino.SnakeR(self.feature_type, self.num_features, self.num_columns),
                           tetromino.SnakeL(self.feature_type, self.num_features, self.num_columns),
                           tetromino.T(self.feature_type, self.num_features, self.num_columns)]
        self.tetromino_sampler = tetromino.TetrominoSamplerRandom(self.tetrominos)
        self.cleared_lines = 0
        self.cumulative_steps = 0
        self.max_cleared_test_lines = max_cleared_test_lines

    def reset(self):
        self.game_over = False
        self.current_state = state.State(representation=np.zeros((self.num_rows + self.tetromino_size, self.num_columns), dtype=np.int64),
                                         lowest_free_rows=np.zeros(self.num_columns, dtype=np.int64),
                                         num_features=self.num_features,
                                         feature_type="bcts")
        self.tetromino_sampler = tetromino.TetrominoSampler(self.tetrominos)
        self.cleared_lines = 0

    # def is_game_over(self):
    #     if np.any(self.current_state.representation[self.num_rows]):
    #         self.game_over = True

    def make_step(self, action):
        self.game_over = action.terminal_state
        if not self.game_over:
            self.cleared_lines += action.n_cleared_lines
            self.current_state = action

    # def print_board(self, clear_the_output=True):
    #     self.current_state.print_board(clear_the_output=clear_the_output)

    # def print_board(self, stateX, clear_the_output=False):
    #     if clear_the_output:
    #         clear_output(wait=True)
    #     for row_ix in range(stateX.n_legal_rows):
    #         # Start from top
    #         row_ix = stateX.n_legal_rows - row_ix - 1
    #         print("|", end=' ')
    #         for col_ix in range(stateX.num_columns):
    #             if stateX.representation[row_ix, col_ix]:
    #                 print("██", end=' ')
    #             else:
    #                 print("  ", end=' ')
    #         print("|")

    def print_board_to_string(self, stateX, clear_the_output=False, sleep=0):
        if clear_the_output:
            clear_output(wait=True)
        if sleep > 0:
            time.sleep(sleep)
        string = "\n"
        for row_ix in range(stateX.n_legal_rows):
            # Start from top
            row_ix = stateX.n_legal_rows - row_ix - 1
            string += "|"
            for col_ix in range(stateX.num_columns):
                if stateX.representation[row_ix, col_ix]:
                    string += "██"
                else:
                    string += "  "
            string += "|\n"
        print(string)
        return string

    # def print_board_to_string(self, clear_the_output=True, sleep=0):
    #     return self.current_state.print_board_to_string(clear_the_output, sleep)

    # def visualize_falling(self, move_index):


    # def evaluate(self, hard_test=False):
    #     self.reset()
    #     while not self.game_over and self.cleared_lines <= self.max_cleared_test_lines:
    #         current_tetromino = self.tetromino_sampler.next_tetromino()
    #         if hard_test:
    #             chosen_action, action_index = self.agent.choose_action_test_hard(self.current_state, current_tetromino)
    #         else:
    #             chosen_action, action_index = self.agent.choose_action_test(self.current_state, current_tetromino)
    #         self.cleared_lines += chosen_action.n_cleared_lines
    #         self.current_state = chosen_action
    #         self.game_over = self.current_state.terminal_state
    #     return self.cleared_lines

    def play_cbmpi(self, testing_time=0, num_tests=1, num_test_games=0, test_points=None, test_environment=None, hard_test=False):
        self.reset()
        test_results = np.zeros((num_tests, num_test_games))
        tested_weights = np.zeros((num_tests, self.num_features))
        tested_value_weights = np.zeros((num_tests, self.agent.num_value_features + 1))

        # while not self.game_over and test_index < num_tests:
        if num_tests == 0:
            test_index = -1
        else:
            test_index = 0

        while test_index < num_tests:
            self.agent.learn()
            self.cumulative_steps += 1
            if num_tests > 0 and self.cumulative_steps in test_points:
                # print(self.cumulative_steps)
                tested_weights[test_index] = self.agent.policy_weights.copy()
                tested_value_weights[test_index] = self.agent.value_weights.copy()
                if self.verbose:
                    print("tested_weights", tested_weights)
                    print("tested_value_weights", tested_value_weights)
                testing_time_start = time.time()
                if self.verbose:
                    print("TESTING: ", test_index + 1, " out of ", num_tests, " tests.")
                for game_ix in range(num_test_games):
                    test_results[test_index, game_ix] = test_environment.test_agent(hard_test=hard_test)
                    if self.verbose:
                        print("Game ", game_ix, " had ", test_results[test_index, game_ix], " cleared lines.")
                print("Mean: ", np.mean(test_results[test_index, :]), ", Median: ", np.median(test_results[test_index, :]))
                test_index += 1
                testing_time_end = time.time()
                testing_time += testing_time_end - testing_time_start
                if self.verbose:
                    print("Testing took " + str((testing_time_end - testing_time_start) / 60) + " minutes.")
        return test_results, testing_time, tested_weights

    def learn_cbmpi(self, num_iter=1):
        self.reset()
        tested_weights = np.zeros((num_iter, self.num_features))
        tested_value_weights = np.zeros((num_iter, self.agent.num_value_features + 1))
        index = 0
        while index < num_iter:
            self.agent.learn()
            tested_weights[index] = self.agent.policy_weights.copy()
            tested_value_weights[index] = self.agent.value_weights.copy()
            print("tested_weights", tested_weights[index])
            print("tested_value_weights", tested_value_weights[index])
        return tested_weights, tested_value_weights

    def play_hierarchical_learning(self, testing_time, plots_path, plot_analysis_fc, test_every=0, num_tests=1, num_test_games=0,
                                   test_points=None, test_environment=None, episode=0, agent_ix=0, store_features=False):
        self.reset()
        # self.agent.reset_agent()
        test_results = np.zeros((num_tests, num_test_games))
        tested_weights = np.zeros((num_tests, self.num_features))
        weights_storage = np.expand_dims(self.agent.policy_weights, axis=0)
        if num_tests == 0:
            test_index = -1
        else:
            test_index = 0
        while test_index < num_tests:
            # TEST
            if num_tests > 0 and self.cumulative_steps in test_points:  # and self.cumulative_steps > 0
            # if num_tests > 0 and self.cumulative_steps % test_every == 0:  #  and self.cumulative_steps > 0
                tested_weights[test_index] = self.agent.policy_weights
                print("tested_weights", tested_weights)
                testing_time_start = time.time()
                print("TESTING: ", test_index + 1, " out of ", num_tests, " tests.")
                for game_ix in range(num_test_games):
                    test_results[test_index, game_ix] = test_environment.test_agent()
                    print("Game ", game_ix, " had ", test_results[test_index, game_ix], " cleared lines.")
                print("Mean: ", np.mean(test_results[test_index, :]), ", Median: ", np.median(test_results[test_index, :]))
                test_index += 1
                if self.plot_intermediate_results:
                    plot_analysis_fc(plots_path, tested_weights, test_results, weights_storage, agent_ix)
                testing_time_end = time.time()
                testing_time += testing_time_end - testing_time_start
            if self.game_over:
                self.reset()
            if self.verbose:
                print("Episode: ", episode, ", Step: ", self.agent.step, "Cleared lines: ", self.cleared_lines)
            current_tetromino = self.tetromino_sampler.next_tetromino()
            if self.verbose:
                print(current_tetromino)
                self.current_state.print_board()
            bef = time.time()
            chosen_action, action_index, action_features = self.agent.choose_action(start_state=self.current_state,
                                                                                    start_tetromino=current_tetromino)
            af = time.time()
            # if self.verbose:
            print("CURRENT STEP: " + str(self.cumulative_steps))
            print("Choosing an action took: " + str(af-bef) + " seconds.")
            self.game_over = chosen_action.terminal_state

            store_features = store_features if self.agent.phase == "learn_directions" else False
            # Change state
            weights_storage = np.vstack((weights_storage, self.agent.policy_weights))
            self.cleared_lines += chosen_action.n_cleared_lines
            self.current_state = chosen_action

            # LEARN
            if not (self.agent.ew or self.agent.ttb):
                bef = time.time()
                if not self.game_over:
                    print("Started learning")
                    switched_phase = self.agent.learn(action_features=action_features, action_index=action_index)
                    # if self.agent.phase == "learn_directions":
                    #     pass
                    #     # self.agent.push_data(action_features, action_index)
                    #     # print("Direction counts:")
                    #     # print(self.agent.positive_direction_counts / self.agent.meaningful_comparisons - 0.5)
                    #     # print("Learnt directions:")
                    #     # print(self.agent.decided_directions)
                    # elif self.agent.phase == "learn_order":
                    #     print("Current ordering")
                    #     print(np.argsort(-self.agent.positive_direction_counts/self.agent.meaningful_comparisons))
                    #     print("Current ratios")
                    #     print(self.agent.positive_direction_counts/self.agent.meaningful_comparisons)
                    # elif self.agent.phase == "learn_weights":
                    #     # if self.verbose:
                    #     print("CURRENT WEIGHTS:")
                    #     print(self.agent.policy_weights * self.agent.feature_directors)
                    if switched_phase and self.agent.phase != "optimize_weights":
                        print("RESET GAME!")
                        self.reset()
                af = time.time()
                print("Learning took: " + str(af-bef) + " seconds.")
                print("self.agent.mlogit_data.choice_set_counter: " + str(self.agent.mlogit_data.choice_set_counter))
                print("self.agent.mlogit_data.current_number_of_choice_sets: " + str(self.agent.mlogit_data.current_number_of_choice_sets))
            self.agent.step += 1
            self.cumulative_steps += 1
        # if self.game_over:
        #     raise ValueError("Training game should not be over...!!")
        return self.cleared_lines, test_results, testing_time, tested_weights, weights_storage

    def store_moves(self):
        """
        Can be used to store states encountered by
        :return:
        """
        self.reset()
        self.state_samples = []
        while not self.game_over and self.cleared_lines <= self.max_cleared_test_lines:
            current_tetromino = self.tetromino_sampler.next_tetromino()
            if self.verbose:
                print(current_tetromino)
                self.current_state.print_board()
            # self.state_samples.append(self.current_state)
            chosen_action, action_index = self.agent.choose_action_test(self.current_state, current_tetromino)
            if not chosen_action.terminal_state:
                self.state_samples.append(self.current_state)
            self.cleared_lines += chosen_action.n_cleared_lines
            self.current_state = chosen_action
            self.game_over = self.current_state.terminal_state
        return self.cleared_lines, self.state_samples




Sample = collections.namedtuple('Sample', ('state', 'tetromino'))



import threading
import tensorflow as tf
import numpy as np

from deeprl.util.logger import logger
from deeprl.approximators.nn import ActorCriticNN
from deeprl.util.environments import get_env #AtariEnvironment
from deeprl.util.hyper_parameters import *
import pprint

class A3CAgent(object):

    def __init__(self, env_name, global_network, agent_name, session, optimizer):
        """
        Initializes an Asynchronous Advantage Actor-Critic agent (A3C).
        :param env_name:        Name of the environment
        :param global_network:  Global network to use for updates and synchronization
        :param agent_name:      Name of this agent
        :param session:         TensorFlow session
        """
        self.global_network = global_network
        self.env = get_env(env_name) #AtariEnvironment(env_name)
        self.num_actions = self.env.num_actions()
        self.local_network = ActorCriticNN(num_actions=self.num_actions,
                                           network_name=agent_name,
                                           optimizer=optimizer,
                                           session=session,
                                           hyper_parameters=hyper_parameters,
                                           global_network=global_network)
        self._train_thread = threading.Thread(target=self._train, name=agent_name)
        self.t = 1
        self.last_state = self.env.reset()
        self.session = session
        self.agent_name = agent_name

        self.n_episodes = 1

    def get_action(self):
        """
        Returns action that is taken
        """
        pi = self.local_network.get_pi(self.last_state)
        return np.random.choice(self.num_actions, 1, p=pi)[0]

    def state_value(self, observation):
        """
        Returns the state-value
        """
        return self.local_network.get_value(observation)

    def get_value_and_action(self):
        """
        Returns the action and the value
        """
        value, pi = self.session.run(
            [self.local_network.value, self.local_network.pi],
            feed_dict={self.local_network.inputs: [self.last_state]})
        action = np.random.choice(self.num_actions, 1, p=pi[0])
        return value, action

    def update_params(self, n_step_return, actions, states, values):
        """
        Updates the parameters of the global network
        :param n_step_return:   n-step returns
        :param actions:         array of actions
        :param states:          array of states
        """
        global T

        dnn = self.local_network
        self.session.run(dnn.param_update,
                         feed_dict={dnn.n_step_returns: n_step_return,
                                    dnn.actions: actions,
                                    dnn.inputs: states,
                                    dnn.advantage_no_grad: n_step_return - values,
                                    learning_rate: (1 - T / float(hyper_parameters.T_max)) *
                                                   hyper_parameters.learning_rate})

    def train(self):
        """
        Starts a training thread
        """
        logger.info('Starting training')
        self._train_thread.start()

    def synchronize_thread_parameters(self):
        """
        Synhronizes the thread network parameters with the global network
        """
        logger.debug('Synchronizing global parameters')
        #logger.debug('Global thread params: \n{}'.format(self.session.run(self.global_network.theta[0] - self.local_network.theta[0])))
        self.session.run(self.local_network.param_sync)

    def _train(self):
        """
        This is the thread function for a single A3C agent. The pseudo-code can be found in "Asynchronous Methods for
        Reinforcement Learning" by Mnih et al (2016): https://arxiv.org/abs/1602.01783

        It executes the actor-critic method with asynchronous updates and n-step returns in a forward view
        """
        global T
        # Initialize the reward, action and observation arrays
        rewards         = np.zeros(hyper_parameters.t_max, dtype='float')
        actions         = np.zeros(hyper_parameters.t_max, dtype='int')
        values          = np.zeros(hyper_parameters.t_max, dtype='float')
        n_step_targets  = np.zeros(hyper_parameters.t_max, dtype='float')
        states          = np.zeros((hyper_parameters.t_max,) + self.env.state_shape(), dtype='float')

        # Main loop, execute this while T < T_max
        while T < hyper_parameters.T_max:
            # A new batch begins, reset the gradients and synchronize thread-specific parameters
            self.synchronize_thread_parameters()
            # Set t_start to current t
            t_start = self.t

            # Boolean to denote whether the current state is terminal
            terminal_state = False

            # Now take steps following the thread-specific policy given by self.theta and self.theta_v
            while not terminal_state and self.t - t_start != hyper_parameters.t_max:
                # Index of current step
                i = self.t - t_start
                # Set the current observation
                states[i] = self.last_state
                # Get the corresponding value and action. This is done simultaneously such that the approximators only
                # has to perform a single forward pass.
                values[i], actions[i] = self.get_value_and_action()
                # Perform step in environment and obtain rewards and observations
                self.last_state, rewards[i], terminal_state, info = self.env.step(actions[i])
                # Increment time counters
                self.t += 1
                T += 1

                if self.agent_name == 'Agent_0':
                    self.env.env.render()

            # Initialize the n-step return
            n_step_target = 0 if terminal_state else self.state_value(self.last_state)

            # Forward view of n-step returns, start from i == t_max - 1 and go to i == 0
            for i in reversed(range(hyper_parameters.t_max)):
                # Straightforward accumulation of rewards
                n_step_target = rewards[i] + hyper_parameters.gamma * n_step_target
                n_step_targets[i] = n_step_target

            # Now update the global approximator's parameters
            self.update_params(n_step_targets, actions, states, values)
            logger.debug('Parameters updated!')

            if terminal_state:
                logger.info('Terminal state reached: resetting state')
                self.last_state = self.env.reset()


if __name__ == "__main__":
    hyper_parameters = HyperParameters(parse_cmd_args())
    T = 1

    env_name = hyper_parameters.env
    n_threads = hyper_parameters.n_threads

    global_env = get_env(env_name) #AtariEnvironment(env_name)
    num_actions = global_env.num_actions()

    session = tf.InteractiveSession()
    learning_rate = tf.placeholder(tf.float32)

    shared_optimizer = tf.train.RMSPropOptimizer(
        learning_rate=learning_rate,
        decay=hyper_parameters.lr_decay,
        epsilon=hyper_parameters.rms_epsilon)

    global_network = ActorCriticNN(num_actions=num_actions,
                                   network_name='GLOBAL',
                                   hyper_parameters=hyper_parameters,
                                   session=session,
                                   optimizer=shared_optimizer)

    agents = [A3CAgent(env_name, global_network, 'Agent_%d' % i, session, optimizer=shared_optimizer)
              for i in range(n_threads)]

    session.run(tf.initialize_all_variables())
    for agent in agents:
        agent.train()









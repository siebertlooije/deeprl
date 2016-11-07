import tensorflow as tf
import tflearn
import numpy as np

from deeprl.common.logger import logger
#from tflearn.layers import lstm

from deeprl.approximators.layers import lstm, spatialsoftmax
from tensorflow.python.ops.rnn_cell import LSTMStateTuple

from copy import deepcopy

from deeprl.common.tensorflowutils import sequence_mask


class ModelNames:
    A3C_FF      = 'a3c_ff'
    NATURE      = 'nature'
    SMALL_FCN   = 'small_fcn'
    A3C_LSTM    = 'a3c_lstm'
    A3C_FF_SS   = 'a3c_ff_ss'
    A3C_LSTM_SS = 'a3c_lstm_ss'


class ActorCriticNN(object):

    def __init__(self, num_actions, agent_name, optimizer, session, hyper_parameters, global_network=None):
        self.num_actions = num_actions
        self.optimizer = optimizer
        self.global_network = global_network
        self.session = session
        self.beta = hyper_parameters.beta
        self.summaries = []
        self.agent_name = agent_name
        self.model_name = hyper_parameters.model
        self.clip_advantage = hyper_parameters.clip_advantage
        self.recurrent = self.model_name in [ModelNames.A3C_LSTM]

        # Build computational graphs for loss, synchronization of parameters and parameter updates
        with tf.name_scope(agent_name):
            self.build_network(num_actions, hyper_parameters.input_shape)
            with tf.name_scope('Loss'):
                self.build_loss()

            if global_network:
                self.build_param_sync()
                self.build_param_update()

        self.merged_summaries = tf.merge_summary(self.summaries)

    def _nature_model(self):
        """
        This is the more complicated model as taken from the nature paper in 2015.
        :param network_name:   Name of the network
        :return:               The feedforward model (last hidden layer as a graph node)
        """
        with tf.name_scope('Inputs'):
            net = tf.transpose(self.inputs, [0, 2, 3, 1])

        with tf.name_scope('HiddenLayers'):
            net = tflearn.conv_2d(net, 32, 8, strides=4, activation='relu', name='Conv1')
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 4, strides=2, activation='relu', name='Conv2')
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 3, strides=1, activation='relu', name='Conv3')
            self._add_trainable(net)
            net = tflearn.flatten(net)
            net = tflearn.fully_connected(net, 512, activation='relu', name='FC4')
            self._add_trainable(net)

        return net

    def _a3c_ff(self):
        """
        This is the feedforward model taken from "Asynchronous Methods for Reinforcement Learning"
        :param network_name:    Name of the network
        :return:                The feedforward model (last hidden layer) as a graph node
        """
        with tf.name_scope('Inputs'):
            net = tf.transpose(self.inputs, [0, 2, 3, 1])

        with tf.name_scope('HiddenLayers'):
            net = tflearn.conv_2d(net, 32, 8, strides=4, activation='relu', name='Conv1')
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 4, strides=2, activation='relu', name='Conv2')
            self._add_trainable(net)
            net = tflearn.flatten(net)
            net = tflearn.fully_connected(net, 256, activation='relu', name='FC3')
            self._add_trainable(net)

        return net

    def _a3c_ff_ss(self):
        """
        This is the feedforward model taken from
        :param network_name:    Name of the network
        :return:                The feedforward model (last hidden layer) as a graph node
        """
        with tf.name_scope('Inputs'):
            net = tf.transpose(self.inputs, [0, 2, 3, 1])

        with tf.name_scope('HiddenLayers'):
            net = tflearn.conv_2d(net, 32, 8, strides=4, activation='relu', name='Conv1')
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 4, strides=2, activation='linear', name='Conv2')
            self._add_trainable(net)
            net = spatialsoftmax(net)
            net = tflearn.fully_connected(net, 256, activation='relu', name='FC3')
            self._add_trainable(net)

        return net

    def _a3c_lstm(self):
        """
        This is the feedforward model taken from "Asynchronous Methods for Reinforcement Learning"
        :param network_name:    Name of the network
        :return:                The feedforward model (last hidden layer) as a graph node
        """
        with tf.name_scope('LSTMStateInput'):
            # An LSTM layers's 'state' is defined by the activation of the cells 'c' (256) plus the output of the cell
            # 'h' (256), which are both influencing the layer in the forward/backward pass.
            self.initial_state_c = tf.placeholder(tf.float32, shape=[1, 256], name="InitialLSTMState_c")
            self.initial_state_h = tf.placeholder(tf.float32, shape=[1, 256], name="InitialLSTMState_h")
            self.initial_state = LSTMStateTuple(
                self.initial_state_c,
                self.initial_state_h
            )
        with tf.name_scope('SequenceMasking'):
            self.n_steps = tf.placeholder(tf.int32, shape=[])
            seq_mask = tf.reshape(sequence_mask([self.n_steps], maxlen=5, dtype=tf.float32),
                                  [1, 5, 1], name='SequenceMask')
        with tf.name_scope('ForwardInputs'):
            net = tf.transpose(self.inputs, [0, 2, 3, 1])
        with tf.name_scope('HiddenLayers'):
            net = tflearn.conv_2d(net, 32, 8, strides=4, activation='relu', name='Conv1',
                                  weight_decay=0.0, bias_init=0.1)
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 4, strides=2, activation='relu', name='Conv2',
                                  weight_decay=0.0, bias_init=0.1)
            self._add_trainable(net)
            net = tflearn.flatten(net)
            net = tflearn.fully_connected(net, 256, activation='relu', name='FC3',
                                          weight_decay=0.0, bias_init=0.1)
            self._add_trainable(net)
            net = tf.mul(tflearn.reshape(net, [1, 5, 256]), seq_mask, name='MaskedSequence')  # tf.expand_dims(net, 1)
            net, state = lstm(net, 256, initial_state=self.initial_state, return_state=True,
                              name='LSTM4', dynamic=True, return_seq=True)
            #logger.info(net._op.__dict__)
            self._add_trainable(net)
            #net = tflearn.reshape(net, [-1, 256])
            self.lstm_state_variable = state

        self.reset_lstm_state()
        return net


    def _a3c_lstm_ss(self):
        """
        This is the feedforward model taken from "Asynchronous Methods for Reinforcement Learning"
        :param network_name:    Name of the network
        :return:                The feedforward model (last hidden layer) as a graph node
        """
        with tf.name_scope('LSTMStateInput'):
            # An LSTM layers's 'state' is defined by the activation of the cells 'c' (256) plus the output of the cell
            # 'h' (256), which are both influencing the layer in the forward/backward pass.
            self.initial_state_c = tf.placeholder(tf.float32, shape=[1, 256], name="InitialLSTMState_c")
            self.initial_state_h = tf.placeholder(tf.float32, shape=[1, 256], name="InitialLSTMState_h")
            self.initial_state = LSTMStateTuple(
                self.initial_state_c,
                self.initial_state_h
            )
        with tf.name_scope('SequenceMasking'):
            self.n_steps = tf.placeholder(tf.int32, shape=[])
            seq_mask = tf.reshape(sequence_mask([self.n_steps], maxlen=5, dtype=tf.float32),
                                  [1, 5, 1], name='SequenceMask')
        with tf.name_scope('ForwardInputs'):
            net = tf.transpose(self.inputs, [0, 2, 3, 1])
        with tf.name_scope('HiddenLayers'):
            net = tflearn.conv_2d(net, 32, 8, strides=4, activation='relu', name='Conv1')
            self._add_trainable(net)
            net = tflearn.conv_2d(net, 64, 4, strides=2, activation='linear', name='Conv2')
            self._add_trainable(net)
            #net = tflearn.flatten(net)
            net = spatialsoftmax(net)
            net = tflearn.fully_connected(net, 256, activation='relu', name='FC3')
            self._add_trainable(net)
            net = tf.mul(tflearn.reshape(net, [1, 5, 256]), seq_mask, name='MaskedSequence')  # tf.expand_dims(net, 1)
            net, state = lstm(net, 256, initial_state=self.initial_state, return_state=True,
                              name='LSTM4', dynamic=True, return_seq=True)
            #logger.info(net._op.__dict__)
            self._add_trainable(net)
            #net = tflearn.reshape(net, [-1, 256])
            self.lstm_state_variable = state

        self.reset_lstm_state()
        return net

    def reset_lstm_state(self):
        self.lstm_state_numeric = LSTMStateTuple(
            np.zeros([1, 256], dtype='float32'),
            np.zeros([1, 256], dtype='float32')
        )
        self.lstm_first_state_since_update = LSTMStateTuple(
            np.zeros([1, 256], dtype='float32'),
            np.zeros([1, 256], dtype='float32')
        )

    def reset(self):
        if self.recurrent:
            self.reset_lstm_state()

    def _add_trainable(self, layer):
        self.theta += [layer.W, layer.b]

    def _small_fcn(self):
        """
        This network works with the CartPole-v0/v1 environments (sort of)
        :param network_name:    The name of the network
        :return:                The network as a graph node
        """
        with tf.name_scope('HiddenLayers'):
            net = tflearn.fully_connected(self.inputs, 128, activation='tanh', name='FC1')
            self._add_trainable(net)
        return net

    def build_network(self, num_actions, input_shape):
        logger.debug('Input shape: {}'.format(input_shape))
        with tf.name_scope('ForwardInputs'):
            if self.recurrent:
                self.inputs = tf.placeholder(tf.float32, [5] + input_shape)
            else:
                self.inputs = tf.placeholder(tf.float32, [None] + input_shape)
        logger.info('Building network: {}'.format(self.model_name))

        self.theta = []

        if self.model_name == ModelNames.NATURE:
            net = self._nature_model()
        elif self.model_name == ModelNames.A3C_FF:
            net = self._a3c_ff()
        elif self.model_name == ModelNames.A3C_LSTM:
            net = self._a3c_lstm()
        elif self.model_name == ModelNames.A3C_FF_SS:
            net = self._a3c_ff_ss()
        else:
            net = self._small_fcn()

        with tf.name_scope("Outputs"):
            with tf.name_scope("Policy"):
                self.pi = tflearn.fully_connected(net, num_actions, activation='softmax', name='pi_sa')
                self._add_trainable(self.pi)
            with tf.name_scope("Value"):
                self.value = tflearn.fully_connected(net, 1, activation='linear', name='v_s')
                self._add_trainable(self.value)

    def build_param_sync(self):
        with tf.name_scope("ParamSynchronization"):
            self.param_sync = [tf.assign(local_theta, global_theta, use_locking=False)
                               for local_theta, global_theta in zip(self.theta, self.global_network.theta)]

    def build_param_update(self):
        with tf.name_scope("ParamUpdate"):
            '''
            gradients = [tf.clip_by_norm(grad, 40.0)
                         for grad, _ in self.optimizer.compute_gradients(self.loss, var_list=self.theta)]
            self.param_update = self.optimizer.apply_gradients(zip(gradients, self.global_network.theta))
            '''
            self.local_gradients = [tf.clip_by_norm(grad, 20.0) for grad in tf.gradients(self.loss, self.theta)]


    def build_loss(self):
        """
        This function defines the computation graph that is needed to compute the loss of the approximators.
        """

        with tf.name_scope('BackwardInputs'):
            # The n_step_return is an array of length n (where n is the batch size)
            self.n_step_returns = tf.placeholder(tf.float32, [None], name='NStepReturns')
            # The actions attribute is an array of length n
            self.actions = tf.placeholder(tf.int32, [None], name='Actions')
            # The advantage function requires a
            self.advantage_no_grad = tf.placeholder(tf.float32, [None], name='TDErrors')

        # The advantage is simply the estimated value minus the bootstrapped returns
        advantage = self.n_step_returns - self.value
        #advantage_no_grad = self.n_step_returns - tf.stop_gradient(self.value)

        if self.clip_advantage:
            # I empirically found that it might help to clip the advantage that is used for the policy loss. This might
            # improve stability and consistency of the gradients
            logger.info("Clipping advantage in graph")
            self.advantage_no_grad = tf.clip_by_value(self.advantage_no_grad, -1., 1., name="ClippedAdvantage")

        with tf.name_scope("PolicyLoss"):
            # action matrix is n x a where each row corresponds to a time step and each column to an action
            action_mask = tf.one_hot(self.actions, self.num_actions, 1.0, 0.0, name="ActionMask")
            # self.pi and log_pi are n x a matrices
            log_pi = tf.log(tf.clip_by_value(self.pi, 1e-20, 1.0), name="LogPi")
            # The entropy is added to encourage exploration
            entropy = -tf.reduce_sum(log_pi * self.pi, reduction_indices=1, name="Entropy")

            # Define the loss for the policy (minus is needed to perform *negative* gradient descent == gradient ascent)
            pi_loss = tf.neg(tf.reduce_sum(action_mask * log_pi, reduction_indices=1) * self.advantage_no_grad
                             + self.beta * entropy, name='PiLoss')

        with tf.name_scope("ValueLoss"):
            value_loss = tf.square(advantage)

        # We can combine the policy loss and the value loss in a single expression
        with tf.name_scope("CombinedLoss"):
            if self.recurrent:
                with tf.name_scope("SequenceMasking"):
                    seq_mask = sequence_mask([self.n_steps], maxlen=5, dtype=tf.float32, name='SequenceMaskLoss')
                    pi_loss = tf.mul(seq_mask, pi_loss, name='MaskedPiLoss')
                    value_loss = tf.mul(seq_mask, value_loss, name='MaskedValueLoss')
            self.loss = tf.reduce_mean(pi_loss + 0.5 * value_loss, name='Loss')
            self.summaries.append(tf.scalar_summary('{}/Loss'.format(self.agent_name), self.loss))
            self.summaries.append(tf.scalar_summary('{}/MaxAbsValue'.format(self.agent_name),
                                                    tf.reduce_max(tf.abs(self.value))))

    def get_action(self, state):
        """
        This function returns a single array reflecting the stochastic policy pi for the given state.
        :param state: The input state
        :return: State's policy
        """
        pi = self.session.run(self.pi, feed_dict={self.inputs: [state]})[0]
        return np.random.choice(self.num_actions, p=pi)

    def get_value(self, state):
        """
        This function returns a single value that corresponds to the critic's valuation of the given state.
        :param state: The input state
        :return: State's value
        """
        if self.recurrent:
            # If we use a recurrent model
            return self.session.run(self.value,
                                    feed_dict={
                                        self.inputs: [state] + 4 * [np.zeros_like(state)],
                                        self.initial_state: self.lstm_state_numeric,
                                        self.n_steps: 1
                                    })[0][0]

        return self.session.run(self.value, feed_dict={self.inputs: [state]})[0][0]

    def get_value_and_action(self, state):
        """
        Returns the action and the value
        """
        if self.recurrent:
            value, pi, self.lstm_state_numeric = self.session.run(
                [
                    self.value, self.pi, self.lstm_state_variable
                ],
                feed_dict={
                    self.inputs: [state] + 4 * [np.zeros_like(state)],
                    self.initial_state: self.lstm_state_numeric,
                    self.n_steps: 1
                }
            )
        else:
            value, pi = self.session.run(
                [self.value, self.pi],
                feed_dict={self.inputs: [state]})

        action = np.random.choice(self.num_actions, p=pi[0])
        return value[0][0], action

    def update_params(self, n_step_return, actions, states, values, learning_rate_var, lr):
        """
        Updates the parameters of the global network
        :param n_step_return:       n-step returns
        :param actions:             array of actions
        :param states:              array of states
        :param values:              array of values
        :param learning_rate_var:   learning rate placeholder,
        :param lr:                  actual learning rate
        """
        if self.recurrent:
            # First we need to pad the sequences we got
            n_steps = len(n_step_return)
            n_pad = 5 - n_steps
            pad1d = np.zeros(n_pad)
            n_step_return   = np.concatenate([n_step_return, pad1d])
            actions         = np.concatenate([actions, pad1d])
            values          = np.concatenate([values, pad1d])
            states          = np.concatenate([states, np.zeros((5 - n_steps,) + states[0].shape)])

            # Now we update our parameters AND we take the lstm_state
            gradients, summaries, self.lstm_state_numeric = self.session.run([
                self.local_gradients,
                self.merged_summaries,
                self.lstm_state_variable,
            ],
                feed_dict={
                    self.n_step_returns: n_step_return,
                    self.actions: actions,
                    self.inputs: states,
                    self.advantage_no_grad: n_step_return - values,
                    self.initial_state: self.lstm_first_state_since_update,
                    self.n_steps: n_steps
                }
            )

            #logger.info("Average gradient: {}".format(np.mean(gradients[0])))
            fdict = {opt_grad: grad for opt_grad, grad in zip(self.optimizer.gradients, gradients)}
            fdict[self.optimizer.learning_rate] = lr
            self.session.run(self.optimizer.minimize, feed_dict=fdict)
            # We also need to remember the LSTM state for the next backward pass
            self.lstm_first_state_since_update = deepcopy(self.lstm_state_numeric)
        else:
            # Update the parameters
            gradients, summaries = self.session.run([
                self.local_gradients,
                self.merged_summaries
            ],
                feed_dict={self.n_step_returns: n_step_return,
                           self.actions: actions,
                           self.inputs: states,
                           self.advantage_no_grad: n_step_return - values}
            )

            fdict = {opt_grad: grad for opt_grad, grad in zip(self.optimizer.gradients, gradients)}
            fdict[self.optimizer.learning_rate] = lr
            self.session.run(self.optimizer.minimize, feed_dict=fdict)



        return summaries
        #writer.add_summary(summaries, t)

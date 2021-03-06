import theano
import numpy as np
#np.set_printoptions(threshold=np.nan)
import theano.tensor as T
from theano import Param
import time
import json
from test_utils import *
from cnn_trainer.train_set_iterator import TrainSetIterator
from cnn.hidden_layer import HiddenLayer
from cnn.conv_layer import ConvPoolLayer
from cnn.logreg_layer import LogisticRegressionLayer


class ConvNet(object):
    def __init__(self, nkerns, recept_width, pool_width,
                 dropout_prob, training_batch_size, activation, n_timesteps=1000, dim=18):

        if activation == 'tanh':
            activation_function = lambda x: T.tanh(x)
        elif activation == 'relu':
            activation_function = lambda x: T.maximum(0.0, x)
        else:
            raise ValueError('unknown activation function')

        self.training_batch_size = training_batch_size

        rng = np.random.RandomState(23455)

        self.training_mode = T.iscalar('training_mode')
        self.x = T.matrix('x')
        self.y = T.bvector('y')
        self.batch_size = theano.shared(self.training_batch_size)

        # 18@1*1000
        self.layer0_input = self.x.reshape((self.batch_size, dim, 1, n_timesteps))

        # image 18 @ 1*1000
        # c1: nkerns[0] @ 1* (1000 - recept_width[0] + 1)
        # s2: nkerns[0] @ 1 * c1 / pool_width[0]
        layer0 = ConvPoolLayer(rng, input=self.layer0_input,
                               image_shape=(None, dim, 1, n_timesteps),
                               filter_shape=(nkerns[0], dim, 1, recept_width[0]),
                               poolsize=(1, pool_width[0]), activation_function=activation_function)


        # c3: nkerns[1] @ 1 * (s2 - recept_width[1] + 1)
        # s4  nkerns[1] @ 1 *  c3 / pool_width
        input_layer1_width = (n_timesteps - recept_width[0] + 1) / pool_width[0]
        layer1 = ConvPoolLayer(rng, input=layer0.output,
                               image_shape=(None, nkerns[0], 1, input_layer1_width),
                               filter_shape=(nkerns[1], nkerns[0], 1, recept_width[1]),
                               poolsize=(1, pool_width[1]), activation_function=activation_function)

        # s4:(batch_size, nkerns[1], 1, s4) -> flatten(2) -> (batch_size, nkerns[1]* 1 * s4)
        layer2_input = layer1.output.flatten(2)

        input_layer2_size = (input_layer1_width - recept_width[1] + 1) / pool_width[1]
        # c5: 120@1*1
        self.layer2 = HiddenLayer(rng=rng, input=layer2_input,
                                  n_in=nkerns[1] * 1 * input_layer2_size, n_out=nkerns[2],
                                  training_mode=self.training_mode,
                                  dropout_prob=dropout_prob, activation_function=activation_function)
        # f6/output
        self.layer3 = LogisticRegressionLayer(input=self.layer2.output, n_in=nkerns[2], n_out=2,
                                              training_mode=self.training_mode, dropout_prob=dropout_prob)

        self.params = self.layer3.params + self.layer2.params + layer1.params + layer0.params

    def validate(self, train_set, valid_set, init_learning_rate, max_iters, validation_frequency,
                 improvement_threshold):

        train_set_iterator = TrainSetIterator(train_set, self.training_batch_size)
        n_batches = train_set_iterator.get_number_of_batches()
        print 'training set \nshape:', train_set[1].shape, 'number of seizures:', np.sum(
            train_set[1]), 'number of batches:', n_batches

        valid_set_x, valid_set_y = valid_set
        valid_size = valid_set_x.shape[0]
        print 'validation set \nshape:', valid_size, 'number of seizures:', np.sum(valid_set[1])

        learning_rate = theano.shared(np.float32(init_learning_rate))
        learning_rate_decay = np.float32(init_learning_rate / max_iters)

        cost = self.layer3.negative_log_likelihood(self.y)
        grads = T.grad(cost, self.params)

        #self._check_num_gradient(train_set_iterator.next())
        #updates = self._momentum_updates(grads, learning_rate)
        #updates = self._rmsprop_updates(grads,learning_rate)

        updates = self._vanilla_updates(grads, learning_rate)

        #-------------------- FUNCTIONS
        tp, tn = self.layer3.tptn(self.y)
        fp, fn = self.layer3.fpfn(self.y)

        train_model = theano.function([self.x, self.y, Param(self.training_mode, default=1)],
                                      [cost, self.layer3.p_y_given_x, self.layer2.output], updates=updates,
                                      on_unused_input='ignore')
        validate_model = theano.function([self.x, self.y, Param(self.training_mode, default=0)],
                                         [cost, tp, tn, fp, fn],
                                         on_unused_input='ignore')
        #------------------------------  TRAINING
        iter = 0
        epoch = 0
        best_cost = np.inf
        best_iter = 0
        patience_increase = 2
        patience = 150 * validation_frequency #50
        done_looping = False
        start_time = time.clock()
        while not done_looping:
            epoch += 1
            for x, y in train_set_iterator:
                iter += 1
                train_model(x, y)
                learning_rate.set_value(max(learning_rate.get_value() - learning_rate_decay, 0.0))
                # ------------------------ VALIDATION
                if iter % validation_frequency == 0:
                    self.batch_size.set_value(valid_size)
                    [valid_cost, tp, tn, fp, fn] = validate_model(valid_set_x, valid_set_y)
                    print epoch, iter, tp, tn, fp, fn, valid_cost, learning_rate.get_value()

                    self.batch_size.set_value(self.training_batch_size)

                    if valid_cost < best_cost:
                        if valid_cost < best_cost * improvement_threshold:
                            patience = max(patience, iter * patience_increase)
                        best_iter = iter
                        best_cost = valid_cost

                    if iter >= max_iters or patience <= iter:
                        done_looping = True
                        break

        print 'time:', (time.clock() - start_time) / 60.
        print 'best_iter:', best_iter
        return best_iter

    def test(self, train_set, test_set, init_learning_rate, learning_rate_decay, opt_iters, out_file):
        train_set_iterator = TrainSetIterator(train_set, self.training_batch_size)
        n_batches = train_set_iterator.get_number_of_batches()
        print 'training set \nshape:', train_set[1].shape, 'number of seizures:', np.sum(
            train_set[1]), 'number of batches:', n_batches

        test_set_x, test_set_y = test_set
        test_size = test_set_x.shape[0]
        print 'test set \nshape:', test_size, 'number of seizures:', np.sum(test_set[1])

        learning_rate = theano.shared(np.float32(init_learning_rate))
        learning_rate_decay = np.float32(learning_rate_decay)

        cost = self.layer3.negative_log_likelihood(self.y)
        grads = T.grad(cost, self.params)
        updates = self._vanilla_updates(grads, learning_rate)

        #----------- FUNCTIONS
        tp, tn = self.layer3.tptn(self.y)
        fp, fn = self.layer3.fpfn(self.y)
        tp_idx = self.layer3.tp_idx(self.y)
        fp_idx = self.layer3.fp_idx(self.y)

        train_model = theano.function([self.x, self.y, Param(self.training_mode, default=1)], cost, updates=updates,
                                      on_unused_input='ignore')
        test_model = theano.function([self.x, self.y, Param(self.training_mode, default=0)],
                                     [tp_idx, fp_idx, tp, tn, fp, fn],
                                     on_unused_input='ignore')

        iter = 0
        done_looping = False
        #------------------------------  TRAINING
        while not done_looping:
            for x, y in train_set_iterator:
                iter += 1
                train_model(x, y)
                learning_rate.set_value(max(learning_rate.get_value() - learning_rate_decay, 0.0))
                if iter > opt_iters:
                    done_looping = True
                    break
                    #------------------------------  TESTING
        self.batch_size.set_value(test_size)
        [tp_idx, fp_idx, tp, tn, fp, fn] = test_model(test_set_x, test_set_y)
        seizure_idx = np.flatnonzero(test_set_y)
        det_dict = detections_and_delay(tp_idx,fp_idx,seizure_idx)
        print '-- TEST --'
        print 'tp:', tp, 'tn:', tn, 'fp:', fp, 'fn', fn
        print 'fp indices:', fp_idx, 'tp indices:', tp_idx
        print 'seizure indices:', seizure_idx
        print det_dict
        json.dump(det_dict, out_file)
        out_file.write('\n')

    def _vanilla_updates(self, grads, learning_rate):
        updates = []
        for param_i, grad_i in zip(self.params, grads):
            updates.append((param_i, param_i - learning_rate * grad_i))
        return updates

    def _momentum_updates(self, grads, learning_rate):
        print 'momentum'
        velocity = [theano.shared(np.zeros_like(param_i.get_value())) for param_i in self.params]
        momentum = np.float32(0.5)
        updates = []
        for param_i, grad_i, velocity_i in zip(self.params, grads, velocity):
            new_velocity_i = momentum * velocity_i - learning_rate * grad_i
            updates.append((param_i, param_i + new_velocity_i))
            updates.append((velocity_i, new_velocity_i))
        return updates

    def _rmsprop_updates(self, grads, learning_rate):
        ms = [theano.shared(np.zeros_like(param_i.get_value())) for param_i in self.params]
        updates = []
        for param_i, grad_i, ms_i in zip(self.params, grads, ms):
            ms_next_i = 0.9 * ms_i + 0.1 * grad_i ** 2
            updates.append((param_i, param_i - learning_rate * grad_i / (T.sqrt(ms_next_i))))
            updates.append((ms_i, ms_next_i))
        return updates
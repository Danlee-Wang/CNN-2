import theano
import numpy as np
import theano.tensor as T
from theano import Param
from cnn_trainer.train_set_iterator import TrainSetIterator
from cnn.hidden_layer import HiddenLayer
from cnn.conv_layer import ConvPoolLayer
from  cnn.logreg_layer import LogisticRegressionLayer


class ConvNet(object):
    def __init__(self, nkerns, recept_width, pool_width,
                 dropout_prob, n_timesteps=1000, dim=18):

        rng = np.random.RandomState(23455)

        self.training_mode = T.iscalar('training_mode')
        self.x = T.matrix('x')
        self.y = T.bvector('y')
        self.batch_size = theano.shared(1)

        # 18@1*1000
        layer0_input = self.x.reshape((self.batch_size, dim, 1, n_timesteps))

        # image 18 @ 1*1000
        # c1: nkerns[0] @ 1* (1000 - recept_width[0] + 1)
        # s2: nkerns[0] @ 1 * c1 / pool_width[0]
        layer0 = ConvPoolLayer(rng, input=layer0_input,
                               image_shape=(None, dim, 1, n_timesteps),
                               filter_shape=(nkerns[0], dim, 1, recept_width[0]),
                               poolsize=(1, pool_width[0]))


        # c3: nkerns[1] @ 1 * (s2 - recept_width[1] + 1)
        # s4  nkerns[1] @ 1 *  c3 / pool_width
        input_layer1_width = (n_timesteps - recept_width[0] + 1) / pool_width[0]
        layer1 = ConvPoolLayer(rng, input=layer0.output,
                               image_shape=(None, nkerns[0], 1, input_layer1_width),
                               filter_shape=(nkerns[1], nkerns[0], 1, recept_width[1]),
                               poolsize=(1, pool_width[1]))

        # s4:(batch_size, nkerns[1], 1, s4) -> flatten(2) -> (batch_size, nkerns[1]* 1 * s4)
        layer2_input = layer1.output.flatten(2)

        input_layer2_size = (input_layer1_width - recept_width[1] + 1) / pool_width[1]
        # c5: 120@1*1
        layer2 = HiddenLayer(rng=rng, input=layer2_input,
                             n_in=nkerns[1] * 1 * input_layer2_size, n_out=nkerns[2],
                             dropout_prob=dropout_prob)
        # f6/output
        self.layer3 = LogisticRegressionLayer(input=layer2.output, n_in=nkerns[2], n_out=2,
                                              training_mode=self.training_mode, dropout_prob=dropout_prob)

        self.params = self.layer3.params + layer2.params + layer1.params + layer0.params

    def validate(self, train_set, valid_set, init_learning_rate, learning_rate_decay, max_epochs,
                 max_fails, improvement_threshold, valids_per_epoch):

        train_set_iterator = TrainSetIterator(train_set)
        n_batches = train_set_iterator.get_number_of_batches()

        valid_set_x, valid_set_y = valid_set
        valid_size = valid_set_x.shape[0]

        learning_rate_decay = np.float32(learning_rate_decay)
        learning_rate = theano.shared(np.float32(init_learning_rate))

        cost = self.layer3.negative_log_likelihood(self.y)
        grads = T.grad(cost, self.params)

        updates = []
        for param_i, grad_i in zip(self.params, grads):
            updates.append((param_i, param_i - learning_rate * grad_i))

        mean_square = [theano.shared(np.zeros_like(param_i.get_value())) for param_i in self.params]
        for ms in mean_square:
            print ms.get_value().shape
        #
        #
        # updates = []
        # for param_i, grad_i, mean_square_i in zip(self.params, grads, mean_square):
        #     updates.append((param_i, param_i - learning_rate * grad_i / T.sqrt(0.9 * mean_square_i + 0.1 * grad_i ** 2)))
        #     updates.append((mean_square_i, 0.9 * mean_square_i + 0.1 * grad_i ** 2))

        #----------- FUNCTIONS
        check_idx = 0
        eps = 0.001
        orig = self.params[check_idx].get_value()
        f_cost = theano.function([self.x, self.y, Param(self.training_mode, default=1)], cost,
                                      on_unused_input='ignore')
        f_grad = theano.function([self.x, self.y, Param(self.training_mode, default=1)], grads,
                                      on_unused_input='ignore')

        orig[0,0] += eps
        self.params[check_idx].set_value(orig)
        cost_plus = 0
        for i in train_set_iterator:
            cost_plus = f_cost(i[0], i[1])
            break

        orig[0,0] -= 2*eps
        self.params[check_idx].set_value(orig)
        cost_minus = 0
        for i in train_set_iterator:
            cost_minus = f_cost(i[0], i[1])
            break

        grad_num = (cost_plus - cost_minus)/ (2*eps)
        print grad_num

        orig[0,0] += eps
        self.params[check_idx].set_value(orig)
        for i in train_set_iterator:
            print f_grad(i[0], i[1])[check_idx]
            break
        return


        weighted_cost = self.layer3.weighted_negative_log_likelihood(self.y)
        ber = self.layer3.ber(self.y)
        tp, tn = self.layer3.tptn(self.y)
        fp, fn = self.layer3.fpfn(self.y)

        train_model = theano.function([self.x, self.y, Param(self.training_mode, default=1)], cost, updates=updates,
                                      on_unused_input='ignore')
        validate_model = theano.function([self.x, self.y, Param(self.training_mode, default=0)],
                                         [weighted_cost, cost, tp, tn, fp, fn],
                                         on_unused_input='ignore')

        #------------------------------  TRAINING
        max_fails = max_fails
        improvement_threshold = improvement_threshold
        validation_frequency = n_batches / valids_per_epoch

        best_weighted_cost = np.inf
        best_weighted_iter = 0
        best_cost = np.inf
        best_iter = 0

        best_ber = np.inf
        best_ber_iter = 0

        iter = 0
        epoch = 0
        fails = 0
        done_looping = False

        while (epoch < max_epochs) and (not done_looping):
            epoch += 1
            if epoch % 2 == 0:
                learning_rate.set_value(max(learning_rate.get_value() / 2.0, 0.01))

            for batch in train_set_iterator:
                iter += 1
                g = f_grad(batch[0], batch[1])
                print g[check_idx]
                # for gg in g:
                #     print gg.shape
                #     print np.where(gg==0)
                # print '================================================='
                return
                train_model(batch[0], batch[1])

                # learning_rate.set_value(max(learning_rate.get_value() - learning_rate_decay, 0.01))
                # ------------------------ VALIDATION
                if iter % validation_frequency == 0:
                    self.batch_size.set_value(valid_size)
                    [weighted_cost, cost, tp, tn, fp, fn] = validate_model(valid_set_x, valid_set_y)
                    print epoch, iter, tp, tn, fp, fn, cost, learning_rate.get_value()

                    self.batch_size.set_value(1)

                    if weighted_cost < best_weighted_cost:
                        best_weighted_cost = weighted_cost
                        best_weighted_iter = iter

                    fails = 0 if cost < best_cost * improvement_threshold else fails + 1
                    if cost < best_cost:
                        best_iter = iter
                        best_cost = cost

                    if fails >= max_fails:
                        done_looping = True
                        break

        print('Optimization complete.')
        print 'Best weighted cost', best_weighted_cost
        print 'Best weighted iteration', best_weighted_iter
        print 'Best cost', best_cost
        print 'Best iteration', best_iter

        return best_cost

    def test(self, train_set, test_set, init_learning_rate, learning_rate_decay):
        learning_rate = init_learning_rate
        train_set_iterator = TrainSetIterator(train_set)
        test_set_x, test_set_y = test_set
        test_size = test_set_x.shape[0]

        cost = self.layer3.negative_log_likelihood(self.y)
        grads = T.grad(cost, self.params)

        updates = []
        for param_i, grad_i in zip(self.params, grads):
            updates.append((param_i, param_i - learning_rate * grad_i))

        #----------- FUNCTIONS
        tp, tn = self.layer3.tptn(self.y)
        fp, fn = self.layer3.fpfn(self.y)

        train_model = theano.function([self.x, self.y, Param(self.training_mode, default=1)], cost, updates=updates,
                                      on_unused_input='ignore')
        test_model = theano.function([self.x, self.y, Param(self.training_mode, default=0)], [tp, tn, fp, fn],
                                     on_unused_input='ignore')

        iter = 0
        epoch = 0
        #------------------------------  TRAINING
        while True:
            epoch += 1
            print 'e', epoch
            for batch in train_set_iterator:
                iter += 1
                train_model(batch[0], batch[1])
                learning_rate -= learning_rate_decay
                if learning_rate < 0:
                    break
                    #------------------------------  TESTING
        self.batch_size.set_value(test_size)
        [tp, tn, fp, fn] = test_model(test_set_x, test_set_y)
        print 'test'
        print 'tp:', tp, 'tn:', tn, 'fp:', fp, 'fn', fn
        print '*********************'

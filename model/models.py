from model.layers import CATS, CATS_Scaled
from data.utils import InputCATSDatasetBuilder
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import tensorflow as tf
import pickle
from sklearn.metrics import roc_auc_score
from sklearn.metrics.pairwise import euclidean_distances
import argparse
import math
import time

class SimilarityClusteringModel(nn.Module):
    def __init__(self, emb_size, m):
        super(SimilarityClusteringModel, self).__init__()
        self.cats = CATS(emb_size)
        self.m = m

    '''
    X is a 3D tensor of shape (n X mC2 X 3*v) where n = num of samples, m = num of paras for each query
    and v = emb vec length
    '''
    def forward(self, X):
        self.pair_score_matrix = self.cats(X)
        tf.sim_matrix = tf.map_fn(self.arrange_simscore_in_sim_matrix, self.pair_score_matrix)
        '''
        TODO
        '''

    def arrange_simscore_in_sim_matrix(self, s):
        assert self.m * (self.m - 1) / 2 == len(s)
        sim_matrix = np.zeros((self.m, self.m))
        n = 0
        for i in range(self.m):
            for j in range(self.m):
                if i==j:
                    sim_matrix[i][j] = 1.0
                elif i<j:
                    sim_matrix[i][j] = s[n]
                    n += 1
                else:
                    sim_matrix[i][j] = sim_matrix[j][i]
        return sim_matrix

class CATSSimilarityModel(nn.Module):
    def __init__(self, emb_size):
        super(CATSSimilarityModel, self).__init__()
        self.cats = CATS(emb_size)

    def forward(self, X):
        self.pair_scores = self.cats(X)
        return self.pair_scores

def run_model(qry_attn_file_train, qry_attn_file_test, train_pids_file, test_pids_file, train_pvecs_file,
              test_pvecs_file, train_qids_file, test_qids_file, train_qvecs_file, test_qvecs_file, use_cache,
              lrate, batch, epochs, save):
    if not use_cache:
        qry_attn_tr = []
        qry_attn_ts = []
        with open(qry_attn_file_train, 'r') as trf:
            f = True
            for l in trf:
                if f:
                    f = False
                    continue
                qry_attn_tr.append(l.split('\t'))
        with open(qry_attn_file_test, 'r') as tsf:
            f = True
            for l in tsf:
                if f:
                    f = False
                    continue
                qry_attn_ts.append(l.split('\t'))
        train_pids = np.load(train_pids_file)
        train_pvecs = np.load(train_pvecs_file)
        train_qids = np.load(train_qids_file)
        train_qvecs = np.load(train_qvecs_file)
        if train_pids_file == test_pids_file:
            test_pids = train_pids
            test_pvecs = train_pvecs
        else:
            test_pids = np.load(test_pids_file)
            test_pvecs = np.load(test_pvecs_file)
        test_qids = np.load(test_qids_file)
        test_qvecs = np.load(test_qvecs_file)

        train_data_builder = InputCATSDatasetBuilder(qry_attn_tr, train_pids, train_pvecs, train_qids, train_qvecs)
        X_train, y_train = train_data_builder.build_input_data()
        test_data_builder = InputCATSDatasetBuilder(qry_attn_ts, test_pids, test_pvecs, test_qids, test_qvecs)
        X_test, y_test = test_data_builder.build_input_data()

        cos = nn.CosineSimilarity(dim=1, eps=1e-6)
        y_cos = cos(X_test[:,768:768*2], X_test[:,768*2:])
        cos_auc = roc_auc_score(y_test, y_cos)
        print('Test cosine auc: '+str(cos_auc))
        y_euclid = euclidean_distances(X_test[:,768:768*2], X_test[:,768*2:])
        y_euclid = (y_euclid-np.min(y_euclid))/(np.max(y_euclid)-np.min(y_euclid))
        euclid_auc = roc_auc_score(y_test, y_euclid)
        print('Test euclidean auc: '+str(euclid_auc))

        val_split_ratio = 0.1
        val_sample_size = int(X_train.shape[0] * val_split_ratio)

        X_val = X_train[:val_sample_size]
        y_val = y_train[:val_sample_size]
        X_train = X_train[val_sample_size:]
        y_train = y_train[val_sample_size:]

        np.save('cache/X_train.npy', X_train)
        np.save('cache/y_train.npy', y_train)
        np.save('cache/X_val.npy', X_val)
        np.save('cache/y_val.npy', y_val)
        np.save('cache/X_test.npy', X_test)
        np.save('cache/y_test.npy', y_test)
    else:
        X_train = torch.tensor(np.load('cache/X_train.npy'))
        y_train = torch.tensor(np.load('cache/y_train.npy'))
        X_val = torch.tensor(np.load('cache/X_val.npy'))
        y_val = torch.tensor(np.load('cache/y_val.npy'))
        X_test = torch.tensor(np.load('cache/X_test.npy'))
        y_test = torch.tensor(np.load('cache/y_test.npy'))

    if torch.cuda.is_available():
        torch.cuda.set_device(torch.device('cuda:0'))
    else:
        torch.cuda.set_device(torch.device('cpu'))
    train_samples = X_train.shape[0]
    X_train = X_train.cuda()
    y_train = y_train.cuda()
    X_val = X_val.cuda()
    y_val = y_val.cuda()
    X_test = X_test.cuda()
    y_test = y_test.cuda()


    m = CATSSimilarityModel(768).cuda()
    opt = optim.Adam(m.parameters(), lr=lrate)
    mseloss = nn.MSELoss()
    for i in range(epochs):
        print('Eppoch '+str(i+1))
        for b in range(math.ceil(train_samples//batch)):
            m.train()
            opt.zero_grad()
            ypred = m(X_train[b*batch:b*batch + batch])
            loss = mseloss(ypred, y_train[b*batch:b*batch + batch])
            auc = roc_auc_score(y_train.detach().cpu().numpy(), ypred.detach().cpu().numpy())
            loss.backward()
            opt.step()
            if b % 10 == 0:
                m.eval()
                ypred_val = m(X_val)
                val_loss = mseloss(ypred_val, y_val)
                val_auc = roc_auc_score(y_val.detach().cpu().numpy(), ypred_val.detach().cpu().numpy())
                print('Train loss: '+str(loss.item())+', Train auc: '+str(auc)+', Val loss: '+str(val_loss.item())+
                      ', Val auc: '+str(val_auc))
    m.eval()
    ypred_test = m(X_test)
    test_loss = mseloss(ypred_test, y_test)
    test_auc = roc_auc_score(y_test.detach().cpu().numpy(), ypred_test.detach().cpu().numpy())
    print('Test loss: '+str(test_loss)+', Test auc: '+str(test_auc))

    if save:
        torch.save(m.state_dict(), 'saved_models/'+time.strftime('%b-%d-%Y_%H%M', time.localtime())+'.model')



def main():
    parser = argparse.ArgumentParser(description='Run CATS model')
    parser.add_argument('-dd', '--data_dir', default="/home/sk1105/sumanta/CATS_data/")
    parser.add_argument('-qtr', '--qry_attn_train', default="half-y1train-qry-attn.tsv")
    parser.add_argument('-qt', '--qtr_attn_test', default="by1test-qry-attn.tsv")
    parser.add_argument('-trp', '--train_pids', default="half-y1train-qry-attn-paraids.npy")
    parser.add_argument('-tp', '--test_pids', default="by1train_by1test_by2test_pids.npy")
    parser.add_argument('-trv', '--train_pvecs', default="half-y1train-qry-attn-paravecs.npy")
    parser.add_argument('-tv', '--test_pvecs', default="by1train_by1test_by2test_vecs.npy")
    parser.add_argument('-trq', '--train_qids', default="half-y1train-context-qids.npy")
    parser.add_argument('-tq', '--test_qids', default="by1test-context-qids.npy")
    parser.add_argument('-trqv', '--train_qvecs', default="half-y1train-context-qvecs.npy")
    parser.add_argument('-tqv', '--test_qvecs', default="by1test-context-qvecs.npy")
    parser.add_argument('-lr', '--lrate', type=float, default=0.0001)
    parser.add_argument('-bt', '--batch', type=int, default=32)
    parser.add_argument('-ep', '--epochs', type=int, default=100)
    parser.add_argument('--cache', action='store_true')
    parser.add_argument('--save', action='store_true')

    args = parser.parse_args()

    run_model(args.qry_attn_train, args.qry_attn_test, args.train_pids, args.test_pids, args.train_pvecs,
              args.test_pvecs, args.train_qids, args.test_qids, args.train_qvecs, args.test_qvecs, args.cache,
              args.lrate, args.batch, args.epochs, args.save)


if __name__ == '__main__':
    main()
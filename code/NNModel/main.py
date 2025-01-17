#coding utf-8

import json, os, math
import random
import argparse

import numpy
import torch
import torch.nn.functional as F
from tqdm import trange
import numpy as np

from data import load_data_instances, DataIterator
from model import MSFAN
import utils

def reset_params(args, model):
    for child in model.children():
        for p in child.parameters():
            if p.requires_grad:
                if len(p.shape) > 1:
                    args.initializer(p)
                else:    
                    stdv = 1. / math.sqrt(p.shape[0])
                    torch.nn.init.uniform_(p, a=-stdv, b=stdv)

def train(args):

    # load double embedding
    word2index = json.load(open(args.prefix + 'doubleembedding/word_idx.json'))
    general_embedding = numpy.load(args.prefix +'doubleembedding/gen.vec.npy')
    general_embedding = torch.from_numpy(general_embedding)
    domain_embedding = numpy.load(args.prefix +'doubleembedding/'+args.dataset+'_emb.vec.npy')
    domain_embedding = torch.from_numpy(domain_embedding)

    # load dataset
    train_sentence_packs = json.load(open(args.prefix + args.dataset + '/train.json'))
    random.shuffle(train_sentence_packs)
    dev_sentence_packs = json.load(open(args.prefix + args.dataset + '/dev.json'))

    instances_train = load_data_instances(train_sentence_packs, word2index, args)
    instances_dev = load_data_instances(dev_sentence_packs, word2index, args)

    random.shuffle(instances_train)
    trainset = DataIterator(instances_train, args)
    devset = DataIterator(instances_dev, args)

    if not os.path.exists(args.model_dir):
        os.makedirs(args.model_dir)

    # build model
    model = MSFAN(general_embedding, domain_embedding, args).to(args.device)

    parameters = list(model.parameters())
    parameters = filter(lambda x: x.requires_grad, parameters)
    optimizer = torch.optim.Adam(parameters, lr=args.lr, weight_decay=args.weight_decay)
    reset_params(args, model)

    # training
    best_joint_f1 = 0
    best_joint_epoch = 0
    for i in range(args.epochs):
        print('Epoch:{}'.format(i))
        for j in trange(trainset.batch_count):
            _, sentence_tokens, lengths, masks, aspect_tags, _, tags = trainset.get_batch(j)
            predictions = model(sentence_tokens, lengths, masks)

            loss = 0.
            tags_flatten = tags[:, :lengths[0], :lengths[0]].reshape([-1])
            prediction_flatten = predictions.reshape([-1, predictions.shape[3]])
            loss = F.cross_entropy(prediction_flatten, tags_flatten, ignore_index=-1)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        joint_precision, joint_recall, joint_f1 = eval(model, devset, args)

        if joint_f1 > best_joint_f1:
            model_path = args.model_dir + args.model + args.task + '.pt'
            torch.save(model, model_path)
            best_joint_f1 = joint_f1
            best_joint_epoch = i
    print('best epoch: {}\tbest dev {} f1: {:.5f}\n\n'.format(best_joint_epoch, args.task, best_joint_f1))


def eval(model, dataset, args):
    model.eval()
    with torch.no_grad():
        predictions=[]
        labels=[]
        all_ids = []
        all_lengths = []
        for i in range(dataset.batch_count):
            sentence_ids, sentence_tokens, lengths, mask, aspect_tags, _, tags = dataset.get_batch(i)
            prediction = model.forward(sentence_tokens,lengths, mask)
            prediction = torch.argmax(prediction, dim=3)
            prediction_padded = torch.zeros(prediction.shape[0], args.max_sequence_len, args.max_sequence_len)
            prediction_padded[:, :prediction.shape[1], :prediction.shape[1]] = prediction
            predictions.append(prediction_padded)

            all_ids.extend(sentence_ids)
            labels.append(tags)
            all_lengths.append(lengths)

        predictions = torch.cat(predictions,dim=0).cpu().tolist()
        labels = torch.cat(labels,dim=0).cpu().tolist()
        all_lengths = torch.cat(all_lengths, dim=0).cpu().tolist()
        precision, recall, f1 = utils.score_uniontags(args, predictions, labels, all_lengths, ignore_index=-1)

        aspect_results = utils.score_aspect(predictions, labels, all_lengths, ignore_index=-1)
        opinion_results = utils.score_opinion(predictions, labels, all_lengths, ignore_index=-1)
        print('Aspect term\tP:{:.5f}\tR:{:.5f}\tF1:{:.5f}'.format(aspect_results[0], aspect_results[1], aspect_results[2]))
        print('Opinion term\tP:{:.5f}\tR:{:.5f}\tF1:{:.5f}'.format(opinion_results[0], opinion_results[1], opinion_results[2]))
        print(args.task+'\tP:{:.5f}\tR:{:.5f}\tF1:{:.5f}\n'.format(precision, recall, f1))

    model.train()
    return precision, recall, f1


def test(args):
    print("Evaluation on testset:")
    model_path = args.model_dir + args.model + args.task + '.pt'
    model = torch.load(model_path).to(args.device)
    model.eval()

    word2index = json.load(open(args.prefix + 'doubleembedding/word_idx.json'))
    sentence_packs = json.load(open(args.prefix + args.dataset + '/test.json'))
    instances = load_data_instances(sentence_packs, word2index, args)
    testset = DataIterator(instances, args)
    eval(model, testset, args)


if __name__=='__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('--prefix', type=str, default="../../data/",
                        help='dataset and embedding path prefix')
    parser.add_argument('--model_dir', type=str, default="savemodel/",
                        help='model path prefix')
    parser.add_argument('--task', type=str, default="triplet", choices=["pair", "triplet"],
                        help='option: pair, triplet')
    parser.add_argument('--mode', type=str, default="train", choices=["train", "test"],
                        help='option: train, test')
    parser.add_argument('--model', type=str, default="MSFAN", choices=["MSFAN"],
                        help='option: MSFAN')
    parser.add_argument('--dataset', type=str, default="res14",
                        help='dataset')
    parser.add_argument('--max_sequence_len', type=int, default=100,
                        help='max length of a sentence')
    parser.add_argument('--device', type=str, default="cuda",
                        help='gpu or cpu')

    parser.add_argument('--lstm_dim', type=int, default=50,
                        help='dimension of lstm cell')
    parser.add_argument('--hidden_dim', type=int, default=128,
                        help='dimension of lstm cell')
    parser.add_argument('--cnn_dim', type=int, default=256,
                        help='dimension of cnn')

    parser.add_argument('--weight_decay', type=float, default=2e-5,
                        help='weight decay')
    parser.add_argument('--lr', type=float, default=5e-4,
                        help='learning rate')
    parser.add_argument('--batch_size', type=int, default=32,
                        help='bathc size')
    parser.add_argument('--epochs', type=int, default=600,
                        help='training epoch number')
    parser.add_argument('--class_num', type=int, default=6,
                        help='label number')
    parser.add_argument('--span', type=int, default=2,
                        help='max number of words in span')
    
    parser.add_argument('--initializer', default='xavier_uniform_', type=str)
    parser.add_argument('--seed', default=None, type=int, help='set seed for reproducibility')

    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
        numpy.random.seed(args.seed)
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed(args.seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        os.environ['PYTHONHASHSEED'] = str(args.seed)

    initializers = {
        'xavier_uniform_': torch.nn.init.xavier_uniform_,
        'xavier_normal_': torch.nn.init.xavier_normal_,
        'orthogonal_': torch.nn.init.orthogonal_,
    }
    args.initializer = initializers[args.initializer]

    if args.mode == 'train':
        train(args)
        test(args)
    else:
        test(args)


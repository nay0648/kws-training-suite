# Copyright (c) Alibaba, Inc. and its affiliates.
import argparse
import os
import re
import sys

import yaml
from modelscope.utils.audio.audio_utils import update_conf
from modelscope.utils.logger import get_logger

from evaluate.util.KWSEval import kws_eval, loadAnnot
from evaluate.util.KWSROC import kws_roc, get_myprint

DEFAULT_MIC_NUMBER = 2
DEFAULT_REF_NUMBER = 1
DEFAULT_KWS_LOG_LEVEL = 2

SC_PATH = './bin/SoundConnect'
LIB_AFEJAVA_PATH = './lib/AFEJava.jar'
LIB_LIBSIGNAL_PATH = './lib/libsignal.jar'
BASE_POS_EXPERIMENT = 'TianGongExperiment_pos'
BASE_NEG_EXPERIMENT = 'TianGongExperiment_neg'

# neg dataset length ratio, compressed by feature extraction
NEG_LEN_RATIO = 1.0

logger = get_logger()


def batch_roc(work_dir, model_path, fe_conf, roc_dir, test_neg=True):
    pos_data_dir = fe_conf['test_pos_data_dir']
    pos_anno_dir = fe_conf['test_pos_anno_dir']
    if test_neg:
        neg_data_dir = fe_conf['test_neg_data_dir']
        neg_anno_dir = os.path.join(work_dir, os.path.split(neg_data_dir)[1] + '_anno')
        # generate negative annotation
        for wav in list_files(neg_data_dir, '.wav', abs_path=False):
            txt = wav.replace('.wav', '.txt')
            anno_path = os.path.join(neg_anno_dir, txt)
            anno_sub_dir = os.path.dirname(anno_path)
            os.makedirs(anno_sub_dir, exist_ok=True)
            try:
                open(anno_path, 'ab', 0).close()
            except OSError:
                pass

    eval_dir = roc_dir + '_eval'
    threads = fe_conf['workers']
    base_pos_dir = os.path.join(work_dir, BASE_POS_EXPERIMENT)
    base_neg_dir = os.path.join(work_dir, BASE_NEG_EXPERIMENT)
    for d in ('0_input', '1_cut', '2_fewake_eval', '2_wake', '3_asr', '4_asr_eval', '5_chselection_eval', '6_voiceprint', '7_vad_eval'):
        os.makedirs(os.path.join(base_pos_dir, d), exist_ok=True)
        os.makedirs(os.path.join(base_neg_dir, d), exist_ok=True)

    fe_conf_path = os.path.join(os.path.dirname(__file__), 'conf', 'sc.conf')
    for mp in list_files(model_path, '.txt'):
        tmpconfpath = os.path.join(work_dir, 'tmp.conf')

        my_conf = {**fe_conf, 'kws_model': mp}
        update_conf(fe_conf_path, tmpconfpath, my_conf)

        name = os.path.split(mp)[1]
        pos_result_dir = os.path.join(eval_dir, name, 'pos')
        neg_result_dir = os.path.join(eval_dir, name, 'neg')
        os.makedirs(pos_result_dir, exist_ok=True)
        os.makedirs(neg_result_dir, exist_ok=True)
        eval_on_rough_anno(tmpconfpath, base_pos_dir, pos_anno_dir, pos_anno_dir, threads, pos_result_dir)
        if test_neg:
            eval_on_rough_anno(tmpconfpath, base_neg_dir, neg_anno_dir, neg_data_dir, threads, neg_result_dir)
            # eval_on_manual_anno(tmpconfpath, base_neg_dir, neg_data_dir, threads)

        model_roc_file = os.path.join(roc_dir, os.path.split(mp)[1])

        kws_roc(pos_result_dir,
                neg_result_dir,
                os.path.join(base_neg_dir, '0_input'),
                NEG_LEN_RATIO,
                get_myprint(model_roc_file))
        print('DONE: ' + mp)


def list_files(path, ext, abs_path=True):
    path_length = len(path)
    if not path.endswith('/'):
        path_length += 1
    for root, dirs, files in os.walk(path, followlinks=True):
        for file in files:
            if file.endswith(ext):
                if abs_path:
                    yield os.path.join(root, file)
                else:
                    yield os.path.join(root, file)[path_length:]


def eval_on_manual_anno(conf_path, base_experiment, base_data, threads):
    cmd = 'rm -rf ' + base_experiment + '/0_input/*'
    os.system(cmd)
    cmd1 = 'java -cp ' + LIB_AFEJAVA_PATH + ' cc.soundconnect.toolkit.AFEBatch ' + SC_PATH
    cmd1 += ' ' + conf_path
    cmd1 += ' ' + base_data
    cmd1 += ' ' + base_experiment + '/0_input/ --numths ' + str(threads)
    logger.info('cmdline: %s', cmd1)
    os.system(cmd1)
    cmd1 = 'java -cp ' + LIB_AFEJAVA_PATH + ':' + LIB_LIBSIGNAL_PATH
    cmd1 += ' project.tiangong.TianGongExperiment ' + base_experiment + '/wake.conf'
    logger.info('cmdline: %s', cmd1)
    os.system(cmd1)


def eval_on_rough_anno(conf_path, base_experiment, pos_anno, base_data, threads, eval_result_dir):
    """
    perform positive experiment
    confpath:               fe configure file path
    """
    cmd = 'rm -rf ' + base_experiment + '/0_input/*'
    os.system(cmd)
    cmd1 = 'java -cp ' + LIB_AFEJAVA_PATH + ' cc.soundconnect.toolkit.AFEBatch ' + SC_PATH
    cmd1 += ' ' + conf_path
    cmd1 += ' ' + base_data
    cmd1 += ' ' + base_experiment + '/0_input/ --numths ' + str(threads)
    logger.info('cmdline: %s', cmd1)
    os.system(cmd1)
    kws_eval(pos_anno, os.path.join(base_experiment, '0_input'), eval_result_dir)


def check_conf(cfg, use_remote=None):
    work_dir = cfg['work_dir']
    logger.info('Local work dir: %s', work_dir)
    if os.path.exists(work_dir):
        r = input(f'Directory "{work_dir}" already exists. Data may be OVERRIDE. Proceed(Y/n)? ')
        if r.lower() in ('n', 'no'):
            sys.exit(-1)
    else:
        os.makedirs(work_dir)
    # 根据mic, ref数量计算配置参数
    mic_number = cfg.get('mic_number', DEFAULT_MIC_NUMBER)
    ref_number = cfg.get('ref_number', DEFAULT_REF_NUMBER)
    cfg['nummics'] = mic_number
    cfg['numrefs'] = ref_number
    cfg['numins'] = mic_number+ref_number
    # 配置文件中chorder都留空，使用默认顺序，即ref通道排在最后的情况，finetune输出增加一个打标通道
    # cfg['chorder'] = ','.join([str(i) for i in list(range(mic_number+2))])
    cfg['validate_numouts'] = mic_number
    cfg['finetune_numouts'] = mic_number+ref_number+1
    cfg['kws_log_level'] = DEFAULT_KWS_LOG_LEVEL
    # 根据keywords配置拼装关键词
    cfg['kws_decode_desc'] = '\n'.join(cfg['keywords'])
    # 校验正样本标注
    kw_list = [k.split(',')[0] for k in cfg['keywords']]
    annos = loadAnnot(cfg['test_pos_anno_dir'])
    for scene_name, scene in annos.items():
        for file_name, kw in scene.items():
            for kw_name in kw.keys():
                if kw_name not in kw_list:
                    raise RuntimeError(f'Found unknown keyword {kw_name} in scene {scene_name}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='KWS model evaluate script')
    parser.add_argument('config')
    parser.add_argument('model_dir', help='Directory stores model txt files')
    parser.add_argument('-p', '--pos_only', help='Only test positive scenario',
                        action='store_true')

    parser.add_argument('-c', '--confidence', default='0.0',
                        help='The threshold of kws confidence between [0.0, 1.0]',)
    parser.add_argument(
        '-o',
        '--output_dir',
        help='Directory stores evaluation result, default:<config.work_dir>/roc')
    args = parser.parse_args()

    conf_file = args.config
    if not os.path.exists(conf_file):
        logger.error('Config file "%s" is not exist!', conf_file)
        sys.exit(-1)
    logger.info('Loading config from %s', conf_file)
    confidence = float(args.confidence)
    if confidence > 1 or confidence < 0:
        raise ValueError(f'The confidence {confidence} is invalid!')
    with open(conf_file, encoding='utf-8') as f:
        conf = yaml.safe_load(f)
    conf['kws_level'] = args.confidence
    check_conf(conf)
    work_dir = conf['work_dir']

    model_dir = args.model_dir
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(work_dir, 'roc')
    os.makedirs(output_dir, exist_ok=True)
    batch_roc(work_dir, model_dir, conf, output_dir, not args.pos_only)

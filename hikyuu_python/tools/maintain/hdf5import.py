#!/usr/bin/python
# -*- coding: utf8 -*-
# cp936

import os.path
import struct
import sqlite3
import datetime
import math
import sys

import tables as tb

from io import SEEK_END, SEEK_SET

from common import *

def ProgressBar(cur, total):
    percent = '{:.0%}'.format(cur / total)
    sys.stdout.write('\r')
    sys.stdout.write("[%-50s] %s" % ('=' * int(math.floor(cur * 50 / total)),percent))
    sys.stdout.flush()


class H5Record(tb.IsDescription):
    datetime = tb.UInt64Col()        #IGNORE:E1101
    openPrice = tb.UInt32Col()       #IGNORE:E1101
    highPrice = tb.UInt32Col()       #IGNORE:E1101
    lowPrice = tb.UInt32Col()        #IGNORE:E1101
    closePrice = tb.UInt32Col()      #IGNORE:E1101
    transAmount = tb.UInt64Col()     #IGNORE:E1101
    transCount = tb.UInt64Col()      #IGNORE:E1101
    
class H5Index(tb.IsDescription):
    datetime = tb.UInt64Col()        #IGNORE:E1101
    start    = tb.UInt64Col()        #IGNORE:E1101


def create_database(connect):
    """创建SQLITE3数据库表"""
    try:
        cur = connect.cursor()
        filename = os.getcwd() + '/sqlite_createdb.sql'
        with open(filename, 'r', encoding='utf8') as sqlfile:
            cur.executescript(sqlfile.read())
        connect.commit()
        cur.close()
    except sqlite3.OperationalError:
        print("相关数据表可能已经存在，放弃创建。如需重建，请手工删除相关数据表。")
    except Exception as e:
        raise(e)


def get_marketid(connect, market):
    cur = connect.cursor()
    a = cur.execute("select marketid, market from market where market='{}'".format(market))
    marketid = [i for i in a]
    marketid = marketid[0][0]
    cur.close()
    return marketid


def get_codepre_list(connect, marketid, quotation):
    stktype_list = get_stktype_list(quotation)
    sql = "select codepre, type from coderuletype " \
          "where marketid={marketid} and type in {type_list}"\
        .format(marketid=marketid, type_list=stktype_list)
    cur = connect.cursor()
    a = cur.execute(sql)
    a = a.fetchall()
    cur.close()
    return sorted(a, key=lambda k: len(k[0]), reverse=True)


def tdx_import_stock_name_from_file(connect, filename, market, quotation=None):
    """更新每只股票的名称、当前是否有效性、起始日期及结束日期
        如果导入的代码表中不存在对应的代码，则认为该股已失效

        :param connect: sqlite3实例
        :param filename: 代码表文件名
        :param market: 'SH' | 'SZ'
        :param quotation: 待导入的行情类别，空为导入全部 'stock' | 'fund' | 'bond' | None
    """
    cur = connect.cursor()

    newStockDict = {}
    with open(filename, 'rb') as f:
        data = f.read(50)
        data = f.read(314)
        while data:
            a = struct.unpack('6s 17s 8s 283s', data)
            stockcode = a[0].decode()
            stockname = a[2].decode(encoding='gbk').encode('utf8')
            pos = stockname.find(0x00)
            if pos >= 0:
                stockname = stockname[:pos]
            newStockDict[stockcode] = stockname.decode(encoding='utf8').strip()
            data = f.read(314)

    a = cur.execute("select marketid from market where market = '%s'" % market.upper())
    marketid = [i for i in a]
    marketid = marketid[0][0]

    a = cur.execute("select stockid, code, name, valid from stock where marketid = %i" % marketid)
    a = a.fetchall()
    oldStockDict = {}
    for oldstock in a:
        oldstockid, oldcode, oldname, oldvalid = oldstock[0], oldstock[1], oldstock[2], int(oldstock[3])
        oldStockDict[oldcode] = oldstockid

        # 新的代码表中无此股票，则置为无效
        if (oldvalid == 1) and (oldcode not in newStockDict):
            cur.execute("update stock set valid=0 where stockid=%i" % oldstockid)

        # 股票名称发生变化，更新股票名称;如果原无效，则置为有效
        if oldcode in newStockDict:
            if oldname != newStockDict[oldcode]:
                cur.execute("update stock set name='%s' where stockid=%i" %
                            (newStockDict[oldcode], oldstockid))
            if oldvalid == 0:
                cur.execute("update stock set valid=1, endDate=99999999 where stockid=%i" % oldstockid)

    # 处理新出现的股票
    codepre_list = get_codepre_list(connect, marketid, quotation)

    today = datetime.date.today()
    today = today.year * 10000 + today.month * 100 + today.day
    count = 0
    for code in newStockDict:
        if code not in oldStockDict:
            for codepre in codepre_list:
                length = len(codepre[0])
                if code[:length] == codepre[0]:
                    count += 1
                    #print(market, code, newStockDict[code], codepre)
                    sql = "insert into Stock(marketid, code, name, type, valid, startDate, endDate) \
                           values (%s, '%s', '%s', %s, %s, %s, %s)" \
                          % (marketid, code, newStockDict[code], codepre[1], 1, today, 99999999)
                    cur.execute(sql)
                    break

    print('%s新增股票数：%i' % (market.upper(), count))
    connect.commit()
    cur.close()


def tdx_import_day_data_from_file(connect, filename, h5file, market, stock_record):
    """

    :param connect:
    :param filename:
    :param h5file:
    :param stock_record: (stockid, marketid, code, valid, type)
    :return:
    """
    add_record_count = 0
    if not os.path.exists(filename):
        return add_record_count

    stockid, marketid, code, valid, stktype = stock_record[0], stock_record[1], stock_record[2], stock_record[3],stock_record[4]

    try:
        group = h5file.get_node("/", "data")
    except:
        group = h5file.create_group("/", "data")

    tablename = market.upper() + code
    try:
        table = h5file.get_node(group, tablename)
    except:
        table = h5file.create_table(group, tablename, H5Record)

    if table.nrows > 0:
        startdate = table[0]['datetime']/10000
        lastdatetime = table[-1]['datetime']/10000
    else:
        startdate = None
        lastdatetime = None

    update_flag = False
    row = table.row
    with open(filename, 'rb') as src_file:
        data = src_file.read(32)
        while data:
            record = struct.unpack('iiiiifii', data)
            if lastdatetime and record[0] <= lastdatetime:
                data = src_file.read(32)
                continue

            if 0 not in record[1:5]:
                if record[2] >= record[1] >= record[3] \
                        and record[2] >= record[4] >= record[3]:
                    row['datetime'] = record[0] * 10000
                    row['openPrice'] = record[1] * 10
                    row['highPrice'] = record[2] * 10
                    row['lowPrice'] = record[3] * 10
                    row['closePrice'] = record[4] * 10
                    row['transAmount'] = round(record[5] * 0.001)
                    if stktype == 2:
                        # 指数
                        row['transCount'] = record[6]
                    else:
                        row['transCount'] = round(record[6] * 0.01)

                    row.append()
                    add_record_count += 1
                    if not update_flag:
                        update_flag = True

            data = src_file.read(32)

    if update_flag:
        table.flush()

    if startdate is not None and valid == 0:
        cur = connect.cursor()
        cur.execute("update stock set valid=1, startdate=%i, enddate=%i where stockid=%i" %
                    (startdate, 99999999, stockid))
        connect.commit()
        cur.close()

    return add_record_count


def tdx_import_day_data(connect, market, quotation, src_dir, dest_dir, progress=ProgressBar):
    """
    导入通达信日线数据，只导入基础信息数据库中存在的股票
    """
    add_record_count = 0
    market = market.upper()
    if market == 'SH':
        h5file = tb.open_file(dest_dir + "/sh_day.h5", "a", filters=tb.Filters(complevel=9, complib='zlib', shuffle=True))
    elif market == 'SZ':
        h5file = tb.open_file(dest_dir + "/sz_day.h5", "a", filters=tb.Filters(complevel=9, complib='zlib', shuffle=True))
    else:
        print('Invalid market:', market)
        return add_record_count

    marketid = get_marketid(connect, market)
    stktype_list = get_stktype_list(quotation)
    sql = "select stockid, marketid, code, valid, type from stock where marketid={} and type in {}".format(marketid, stktype_list)

    cur = connect.cursor()
    a = cur.execute(sql)
    a = a.fetchall()
    total = len(a)
    for i, stock in enumerate(a):
        filename = src_dir + "\\" + market.lower() + stock[2]+ ".day"
        #print(i,filename)
        add_record_count += tdx_import_day_data_from_file(connect, filename, h5file, market, stock)
        if progress:
            progress(i, total)

    connect.commit()
    h5file.close()
    return add_record_count


def tdx_import_min_data_from_file(connect, filename, h5file, market, stock_record):
    add_record_count = 0
    if not os.path.exists(filename):
        return add_record_count

    stockid, marketid, code, valid, stktype = stock_record[0], stock_record[1], stock_record[2], stock_record[3],stock_record[4]

    try:
        group = h5file.get_node("/", "data")
    except:
        group = h5file.create_group("/", "data")

    tablename = market.upper() + code
    try:
        table = h5file.get_node(group, tablename)
    except:
        table = h5file.create_table(group, tablename, H5Record)

    if table.nrows > 0:
        lastdatetime = table[-1]['datetime']/10000
    else:
        lastdatetime = None

    update_flag = False
    row = table.row
    with open(filename, 'rb') as src_file:
        def trans_date(yymm, hhmm):
            tmp_date = yymm >> 11
            remainder = yymm & 0x7ff
            year = tmp_date + 2004
            month = remainder // 100
            day = remainder % 100
            hh = hhmm // 60
            mm = hhmm % 60
            return year * 100000000 + month * 1000000 + day * 10000 + hh * 100 + mm

        def get_date(pos):
            src_file.seek(pos * 32, SEEK_SET)
            data = src_file.read(4)
            a = struct.unpack('hh', data)
            return trans_date(a[0], a[1])

        def find_pos():
            src_file.seek(0, SEEK_END)
            pos = src_file.tell()
            total = pos // 32
            if lastdatetime is None:
                return total, 0

            low, high = 0, total - 1
            mid = high // 2
            while mid <= high:
                cur_date = get_date(low)
                if cur_date > lastdatetime:
                    mid = low
                    break

                cur_date = get_date(high)
                if cur_date <= lastdatetime:
                    mid = high + 1
                    break

                cur_date = get_date(mid)
                if cur_date <= lastdatetime:
                    low = mid + 1
                else:
                    high = mid - 1

                mid = (low + high) // 2

            return total, mid

        file_total, pos = find_pos()
        if pos < file_total:
            src_file.seek(pos * 32, SEEK_SET)

            data = src_file.read(32)
            while data:
                record = struct.unpack('hhfffffii', data)
                if 0 not in record[2:6]:
                    if record[3] >= record[2] >= record[4] \
                            and record[3] >= record[5] >= record[4]:
                        row['datetime'] = trans_date(record[0], record[1])
                        row['openPrice'] = record[2] * 1000
                        row['highPrice'] = record[3] * 1000
                        row['lowPrice'] = record[4] * 1000
                        row['closePrice'] = record[5] * 1000
                        row['transAmount'] = round(record[6] * 0.001)
                        if stktype == 2:
                            # 指数
                            row['transCount'] = record[7]
                        else:
                            row['transCount'] = round(record[6] * 0.01)

                        row.append()
                        add_record_count += 1
                        if not update_flag:
                            update_flag = True

                data = src_file.read(32)

    if update_flag:
        table.flush()

    return add_record_count

def tdx_import_data(connect, market, ktype, quotation, src_dir, dest_dir, progress=ProgressBar):
    """
    导入通达信日线数据，只导入基础信息数据库中存在的股票
    """

    add_record_count = 0
    market = market.upper()
    filename = "{}_{}.h5".format(market, ktype)
    filename = "{}/{}".format(dest_dir, filename.lower())
    h5file = tb.open_file(filename, "a", filters=tb.Filters(complevel=9, complib='zlib', shuffle=True))

    if ktype.upper() == "DAY":
        suffix = ".day"
        func_import_from_file = tdx_import_day_data_from_file
    elif ktype.upper() == "1MIN":
        suffix = ".lc1"
        func_import_from_file = tdx_import_min_data_from_file
    elif ktype.upper() == "5MIN":
        suffix = ".lc5"
        func_import_from_file = tdx_import_min_data_from_file

    marketid = get_marketid(connect, market)
    stktype_list = get_stktype_list(quotation)
    sql = "select stockid, marketid, code, valid, type from stock where marketid={} and type in {}".format(marketid, stktype_list)

    cur = connect.cursor()
    a = cur.execute(sql)
    a = a.fetchall()

    total = len(a)
    for i, stock in enumerate(a):
        filename = src_dir + "\\" + market.lower() + stock[2]+ suffix
        #print(i,filename)
        add_record_count += func_import_from_file(connect, filename, h5file, market, stock)
        if progress:
            progress(i, total)

    connect.commit()
    h5file.close()
    return add_record_count


def ImportDayData(connect, src_dir, dest_dir):
    """
    导入通达信日线数据，只导入基础信息数据库中存在的股票
    """
    cur = connect.cursor()
    
    h5fileDict = {'SH': tb.open_file(dest_dir + "\\sh_day.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True)),
                  'SZ': tb.open_file(dest_dir + "\\sz_day.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True))}
    
    h5groupDict = {}
    for market in h5fileDict:
        try:
            group = h5fileDict[market].get_node("/", "data")
        except:
            group = h5fileDict[market].create_group("/", "data")
        h5groupDict[market] = group
    
    dirDict = {'SH': src_dir + "\\vipdoc\\sh\\lday",
               'SZ': src_dir + "\\vipdoc\\sz\lday"}
    
    a = cur.execute("select marketid, market from market")
    marketDict = {}
    for mark in a:
        marketDict[mark[0]] = mark[1].upper()

    a = cur.execute("select stockid, marketid, code, valid, type from stock order by marketid")
    a = a.fetchall()
    stock_count = 0
    record_count = 0
    total = len(a)
    for i, stock in enumerate(a):
        ProgressBar(i+1, total)
        stockid, marketid, code = stock[0], stock[1], stock[2]
        valid, stktype = stock[3], stock[4]
        market = marketDict[marketid]
        tablename = market + code
        filename = dirDict[market] + "\\" + tablename.lower() + ".day"
        
        if not os.path.exists(filename):
            continue
        
        try:
            table = h5fileDict[market].get_node(h5groupDict[market], tablename)
        except:
            table = h5fileDict[market].create_table(h5groupDict[market], tablename, H5Record)
        
        if table.nrows > 0:
            startdate = table[0]['datetime']/10000
            lastdatetime = table[-1]['datetime']/10000
        else:
            startdate = None
            lastdatetime = None 
        
        update_flag = False
        row = table.row
        with open(filename, 'rb') as src_file:
            def get_date(pos):
                src_file.seek(pos * 32, SEEK_SET)
                data = src_file.read(4)
                return  struct.unpack('i', data)[0]
            
            def find_pos():
                src_file.seek(0, SEEK_END)
                pos = src_file.tell()
                total = pos // 32
                if lastdatetime is None:
                    return total, 0
                
                low, high = 0, total - 1
                mid = high // 2
                while mid <= high:
                    cur_date = get_date(low)
                    if cur_date > lastdatetime:
                        mid = low
                        break
                    
                    cur_date = get_date(high)
                    if cur_date <= lastdatetime:
                        mid = high + 1
                        break
                    
                    cur_date = get_date(mid)
                    if cur_date <= lastdatetime:
                        low = mid + 1
                    else: 
                        high = mid - 1
                    
                    mid = (low + high) // 2
                    
                return total, mid
            
            file_total, pos = find_pos()
            if pos < file_total:
                src_file.seek(pos * 32, SEEK_SET)
                
                data = src_file.read(32)
                while data:
                    record = struct.unpack('iiiiifii', data)
                    if 0 not in record[1:5]:
                        if record[2] >= record[1] >= record[3] \
                              and record[2] >= record[4] >= record[3]:
                            row['datetime'] = record[0] * 10000
                            row['openPrice'] = record[1] * 10
                            row['highPrice'] = record[2] * 10
                            row['lowPrice'] = record[3] * 10
                            row['closePrice'] = record[4] * 10
                            row['transAmount'] = round(record[5] * 0.001)
                            if stktype == 2:
                                #指数
                                row['transCount'] = record[6]
                            else:
                                row['transCount'] = round(record[6] * 0.01)
                                
                            row.append()
                            record_count += 1
                            if not update_flag:
                                update_flag = True
                    
                    data = src_file.read(32)
                                
        if update_flag:
            stock_count += 1
            table.flush()
            
        if startdate is not None and valid == 0:
            cur.execute("update stock set valid=1, startdate=%i, enddate=%i where stockid=%i" %
                        (startdate, 99999999, stockid))
        
    connect.commit()
                                
    for market in h5fileDict:
        h5fileDict[market].close()
        
    print("\n共导入股票数:", stock_count)
    print("共导入日线数:", record_count)


def ImportMinData(connect, src_dir, dest_dir, data_type):
    """
    导入通达信分钟线、5分钟线数据，只导入基础信息数据库中存在的股票
    """
    if data_type != '1min' and data_type != '5min':
        print("错误的参数: %s" % data_type)
        return
    
    cur = connect.cursor()
    
    if data_type == '1min':
        print("导入1分钟数据")
        h5fileDict = {'SH': tb.open_file(dest_dir + "\\sh_1min.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True)),
                      'SZ': tb.open_file(dest_dir + "\\sz_1min.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True))}
        dirDict = {'SH': src_dir + "\\vipdoc\\sh\\minline",
                   'SZ': src_dir + "\\vipdoc\\sz\minline"}
        file_suffix = '.lc1'
    else:
        print("导入5分钟数据")
        h5fileDict = {'SH': tb.open_file(dest_dir + "\\sh_5min.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True)),
                      'SZ': tb.open_file(dest_dir + "\\sz_5min.h5", "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True))}
        dirDict = {'SH': src_dir + "\\vipdoc\\sh\\fzline",
                   'SZ': src_dir + "\\vipdoc\\sz\fzline"}
        file_suffix = '.lc5'
        
    
    h5groupDict = {}
    for market in h5fileDict:
        try:
            group = h5fileDict[market].get_node("/", "data")
        except:
            group = h5fileDict[market].create_group("/", "data")
        h5groupDict[market] = group
    
    
    a = cur.execute("select marketid, market from market")
    marketDict = {}
    for mark in a:
        marketDict[mark[0]] = mark[1].upper()

    a = cur.execute("select marketid, code, type from stock order by marketid")
    a = a.fetchall()
    stock_count = 0
    record_count = 0
    total = len(a)
    for i, stock in enumerate(a):
        ProgressBar(i+1, total)
        marketid, code, stktype = stock[0], stock[1], stock[2]
        market = marketDict[marketid]
        tablename = market + code
        filename = dirDict[market] + "\\" + tablename.lower() + file_suffix
        
        if not os.path.exists(filename):
            continue
        
        try:
            table = h5fileDict[market].get_node(h5groupDict[market], tablename)
        except:
            table = h5fileDict[market].create_table(h5groupDict[market], tablename, H5Record)
        
        if table.nrows > 0:
            lastdatetime = table[-1]['datetime']
        else:
            lastdatetime = None 
        
        update_flag = False
        row = table.row
        with open(filename, 'rb') as src_file:
            def trans_date(yymm, hhmm):
                tmp_date = yymm >> 11
                remainder = yymm & 0x7ff
                year = tmp_date + 2004
                month = remainder // 100
                day = remainder % 100
                hh = hhmm // 60
                mm = hhmm % 60
                return year * 100000000 + month * 1000000 + day * 10000 + hh * 100 + mm
            
            def get_date(pos):
                src_file.seek(pos * 32, SEEK_SET)
                data = src_file.read(4)
                a = struct.unpack('hh', data)
                return trans_date(a[0], a[1])
            
            def find_pos():
                src_file.seek(0, SEEK_END)
                pos = src_file.tell()
                total = pos // 32
                if lastdatetime is None:
                    return total, 0
                
                low, high = 0, total - 1
                mid = high // 2
                while mid <= high:
                    cur_date = get_date(low)
                    if cur_date > lastdatetime:
                        mid = low
                        break
                    
                    cur_date = get_date(high)
                    if cur_date <= lastdatetime:
                        mid = high + 1
                        break
                    
                    cur_date = get_date(mid)
                    if cur_date <= lastdatetime:
                        low = mid + 1
                    else: 
                        high = mid - 1
                    
                    mid = (low + high) // 2
                    
                return total, mid
            
            file_total, pos = find_pos()
            if pos < file_total:
                src_file.seek(pos * 32, SEEK_SET)
                
                data = src_file.read(32)
                while data:
                    record = struct.unpack('hhfffffii', data)
                    if 0 not in record[2:6]:
                        if record[3] >= record[2] >= record[4] \
                              and record[3] >= record[5] >= record[4]:
                            row['datetime'] = trans_date(record[0], record[1])
                            row['openPrice'] = record[2] * 1000
                            row['highPrice'] = record[3] * 1000
                            row['lowPrice'] = record[4] * 1000
                            row['closePrice'] = record[5] * 1000
                            row['transAmount'] = round(record[6] * 0.001)
                            if stktype == 2:
                                #指数
                                row['transCount'] = record[7]
                            else:
                                row['transCount'] = round(record[6] * 0.01)
                                
                            row.append()
                            record_count += 1
                            if not update_flag:
                                update_flag = True
                    
                    data = src_file.read(32)
                                
        if update_flag:
            stock_count += 1
            table.flush()
            
    connect.commit()
                                
    for market in h5fileDict:
        h5fileDict[market].close()
        
    print("\n共导入股票数:", stock_count)
    if data_type == '1min':
        print("共导入1分钟线数:", record_count)
    else:
        print("共导入5分钟线数:", record_count)
    

            
def UpdateIndex(filename, data_type):
    
    def getWeekDate(olddate):
        y = olddate//100000000
        m = olddate//1000000 - y*100
        d = olddate//10000 - (y*10000+m*100)
        tempdate = datetime.date(y,m,d)
        #python中周一是第0天，周五的第4天
        tempweekdate = tempdate + datetime.timedelta(tempdate.weekday()+4)
        newdate = tempweekdate.year*100000000 + tempweekdate.month*1000000 + tempweekdate.day*10000
        return newdate

    def getMonthDate(olddate):
        y = olddate//100000000
        m = olddate//1000000 - y*100
        import calendar
        _, d = calendar.month(y, m)
        return(y*100000000 + m*1000000 + d*10000)

    def getQuarterDate(olddate):
        quarterDict={1:3,2:3,3:3,4:6,5:6,6:6,7:9,8:9,9:9,10:12,11:12,12:12}
        d_dict = {3:310000, 6:300000, 9:300000, 12:310000}
        y = olddate//100000000
        m = olddate//1000000 - y*100
        return( y*100000000 + quarterDict[m]*1000000 + d_dict[m])
    
    def getHalfyearDate(olddate):
        halfyearDict={1:6,2:6,3:6,4:6,5:6,6:6,7:12,8:12,9:12,10:12,11:12,12:12}
        d_dict = {6:300000, 12:310000}
        y = olddate//100000000
        m = olddate//1000000 - y*100
        return( y*100000000 + halfyearDict[m]*1000000 + 10000 )
    
    def getYearDate(olddate):
        y = olddate//100000000
        return(y*100000000 + 310000)

    def getMin60Date(olddate):
        mint = olddate-olddate//10000*10000
        if mint<=1030:
            newdate = olddate//10000*10000 + 1030
        elif mint<=1130:
            newdate = olddate//10000*10000 + 1130
        elif mint<=1400:
            newdate = olddate//10000*10000 + 1400
        else:
            newdate = olddate//10000*10000 + 1500
        return newdate
    
    def getMin15Date(olddate):
        mint = olddate-olddate//10000*10000
        if mint<=945:
            newdate = olddate//10000*10000 + 945
        elif mint<=1000:
            newdate = olddate//10000*10000 + 1000
        elif mint<=1015:
            newdate = olddate//10000*10000 + 1015
        elif mint<=1030:
            newdate = olddate//10000*10000 + 1030
        elif mint<=1045:
            newdate = olddate//10000*10000 + 1045
        elif mint<=1100:
            newdate = olddate//10000*10000 + 1100
        elif mint<=1115:
            newdate = olddate//10000*10000 + 1115
        elif mint<=1130:
            newdate = olddate//10000*10000 + 1130
        elif mint<=1315:
            newdate = olddate//10000*10000 + 1315
        elif mint<=1330:
            newdate = olddate//10000*10000 + 1330
        elif mint<=1345:
            newdate = olddate//10000*10000 + 1345
        elif mint<=1400:
            newdate = olddate//10000*10000 + 1400
        elif mint<=1415:
            newdate = olddate//10000*10000 + 1415
        elif mint<=1430:
            newdate = olddate//10000*10000 + 1430
        elif mint<=1445:
            newdate = olddate//10000*10000 + 1445
        else:
            newdate = olddate//10000*10000 + 1500
        return newdate    
    
    def getMin30Date(olddate):
        mint = olddate-olddate//10000*10000
        if mint<=1000:
            newdate = olddate//10000*10000 + 1000
        elif mint<=1030:
            newdate = olddate//10000*10000 + 1030
        elif mint<=1100:
            newdate = olddate//10000*10000 + 1100
        elif mint<=1130:
            newdate = olddate//10000*10000 + 1130
        elif mint<=1330:
            newdate = olddate//10000*10000 + 1330
        elif mint<=1400:
            newdate = olddate//10000*10000 + 1400
        elif mint<=1430:
            newdate = olddate//10000*10000 + 1430
        else:
            newdate = olddate//10000*10000 + 1500
        return newdate    
    
    def getNewDate(index_type, olddate):
        if index_type == 'week':
            return getWeekDate(olddate)
        elif index_type == 'month':
            return getMonthDate(olddate)
        elif index_type == 'quarter':
            return getQuarterDate(olddate)
        elif index_type == 'halfyear':
            return getHalfyearDate(olddate)
        elif index_type == 'year':
            return getYearDate(olddate)
        elif index_type == 'min15':
            return getMin15Date(olddate)
        elif index_type == 'min30':
            return getMin30Date(olddate)
        elif index_type == 'min60':
            return getMin60Date(olddate)
        else:
            return None
    
    
    if data_type != 'day' and data_type != 'min':
        print("非法参数值data_type:", data_type)
        return
    
    print('更新 %s 扩展线索引' % filename)
    h5file = tb.open_file(filename, "a", filters=tb.Filters(complevel=9,complib='zlib', shuffle=True))
    
    if data_type == 'day':
        index_list = ('week', 'month', 'quarter', 'halfyear', 'year')
    else:
        index_list = ('min15', 'min30', 'min60')

    groupDict = {}
    for index_type in index_list:
        try:
            groupDict[index_type] = h5file.get_node("/", index_type)
        except:
            groupDict[index_type] = h5file.create_group("/", index_type)
        
    
    root_group = h5file.get_node("/data")
    table_total = root_group._v_nchildren
    table_count = 0
    for table in root_group._f_walknodes():
        table_count += 1
        ProgressBar(table_count, table_total)
        
        for index_type in index_list:
            try:
                index_table = h5file.get_node(groupDict[index_type],table.name)
            except:
                index_table = h5file.create_table(groupDict[index_type],table.name, H5Index)
    
            total = table.nrows
            if 0 == total:
                continue
    
            index_total = index_table.nrows
            index_row = index_table.row
            if index_total:
                index_last_date = int(index_table[-1]['datetime'])
                last_date = getNewDate(index_type, int(table[-1]['datetime']))
                if index_last_date == last_date:
                    continue
                startix = int(index_table[-1]['start'])
                pre_index_date = int(index_table[-1]['datetime'])
            else:
                startix = 0
                date = int(table[0]['datetime'])
                pre_index_date = getNewDate(index_type,date)
                index_row['datetime'] = pre_index_date
                index_row['start'] = 0
                index_row.append()
                #week_table.flush()
                
            index = startix
            for row in table[startix:]:
                date = int(row['datetime'])
                cur_index_date = getNewDate(index_type, date)
                if cur_index_date != pre_index_date:
                    index_row['datetime'] = cur_index_date
                    index_row['start'] = index
                    index_row.append()
                    pre_index_date = cur_index_date
                index += 1
            index_table.flush()
            
    h5file.close()
    print('\n')

if __name__ == '__main__':   
    
    import time
    starttime = time.time()
    
    src_dir = "D:\\TdxW_HuaTai"
    dest_dir = "c:\\stock"
    
    connect = sqlite3.connect(dest_dir + "\\hikyuu-stock.db")
    create_database(connect)

    #tdx_import_stock_name_from_file(connect, src_dir + "\\T0002\\hq_cache\\shm.tnf", 'SH', 'stock')
    tdx_import_stock_name_from_file(connect, src_dir + "\\T0002\\hq_cache\\szm.tnf", 'SZ', 'stock')

    #add_count = tdx_import_data(connect, 'SH', 'DAY', 'stock', src_dir + "\\vipdoc\\sh\\lday", dest_dir)
    add_count = tdx_import_data(connect, 'SH', '1MIN', 'stock', src_dir + "\\vipdoc\\sh\\minline", dest_dir)
    print("\n",add_count)

    #ImportStockName(connect, src_dir + "\\T0002\\hq_cache\\shm.tnf", 'SH')
    #ImportStockName(connect, src_dir + "\\T0002\\hq_cache\\szm.tnf", 'SZ')
    
    #ImportDayData(connect, src_dir, dest_dir)
    #ImportMinData(connect, src_dir, dest_dir, '5min')
    #ImportMinData(connect, src_dir, dest_dir, '1min')

    #UpdateIndex(dest_dir + "\\sh_day.h5", "day")
    #UpdateIndex(dest_dir + "\\sz_day.h5", "day")
    #UpdateIndex(dest_dir + "\\sh_5min.h5", 'min')
    #UpdateIndex(dest_dir + "\\sz_5min.h5", 'min')
    #UpdateIndex(dest_dir + "\\sh_1min.h5", 'min')
    #UpdateIndex(dest_dir + "\\sz_1min.h5", 'min')

    connect.close()
    
    endtime = time.time()
    print("\nTotal time:")
    print("%.2fs" % (endtime-starttime))
    print("%.2fm" % ((endtime-starttime)/60))
# -*- coding: utf-8 -*-
#
# Copyright 2017 Ricequant, Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from time import sleep
from rqalpha.utils.logger import system_log

from .vnpy import *
from .vnpy_gateway import RQPositionData, RQContractData, RQCommissionData, EVENT_COMMISSION
from .utils import make_underlying_symbol


class RQCtpGateway(CtpGateway):

    def __init__(self, event_engine, gateway_name, login_dict, retry_times=3, retry_interval=1):
        super(CtpGateway, self).__init__(event_engine, gateway_name)

        self.login_dict = login_dict
        self.mdApi = RqCtpMdApi(self)  # 行情API
        self.tdApi = RqCtpTdApi(self)  # 交易API

        self.mdConnected = False  # 行情API连接状态，登录完成后为True
        self.tdConnected = False  # 交易API连接状态

        self.qryEnabled = False  # 是否要启动循环查询

        self.requireAuthentication = False
        self._retry_times = retry_times
        self._retry_interval = retry_interval

        self._commission_buffer = {}

        self._settlement_info_confirmed = False
        self._account_received = False
        self._position_received = False
        self._contract_received = False

    def onSettlementInfoConfirm(self):
        self._settlement_info_confirmed = True

    def onAccount(self, account):
        if not self._account_received:
            super(RQCtpGateway, self).onAccount(account)
        self._account_received = True

    def onPosition(self, pos_dict):
        if not self._position_received:
            event = Event(type_=EVENT_POSITION)
            event.dict_['data'] = pos_dict
            self.eventEngine.put(event)
        self._position_received = True

    def onContract(self, contract_dict):
        if not self._contract_received:
            super(RQCtpGateway, self).onContract(contract_dict)
        self._contract_received = True

    def onCommission(self, commission):
        underlying_symbol = make_underlying_symbol(commission.symbol)
        self._commission_buffer[underlying_symbol] = commission

    def connect(self):
        userID = str(self.login_dict.userID)
        password = str(self.login_dict.password)
        brokerID = str(self.login_dict.brokerID)
        tdAddress = str(self.login_dict.tdAddress)
        mdAddress = str(self.login_dict.mdAddress)

        for i in range(self._retry_times):
            if self.mdApi.loginStatus:
                break
            self.mdApi.connect(userID, password, brokerID, mdAddress)
            sleep(self._retry_interval * (i+1))
        
        for i in range(self._retry_times):
            if self.tdApi.loginStatus:
                break
            self.tdApi.connect(userID, password, brokerID, tdAddress, None, None)
            sleep(self._retry_interval * (i+1))
        self.initQuery()

    def qrySettlementInfoConfirm(self):
        self._settlement_info_confirmed = False
        for i in range(self._retry_times):
            if not self._settlement_info_confirmed:
                self.tdApi.qrySettlementInfoConfirm()
                sleep(self._retry_interval * (i+1))

    def qryAccount(self):
        self._account_received = False
        for i in range(self._retry_times):
            if self._account_received:
                break
            super(RQCtpGateway, self).qryAccount()
            sleep(self._retry_interval * (i+1))

    def qryPosition(self):
        self._position_received = False
        for i in range(self._retry_times):
            if self._position_received:
                break
            super(RQCtpGateway, self).qryPosition()
            sleep(self._retry_interval * (i+1))

    def qryContract(self):
        self._contract_received = False
        for i in range(self._retry_times):
            if self._contract_received:
                break
            self.tdApi.qryInstrument()
            sleep(self._retry_interval * (i+1))

    def qryCommission(self, symbol_list):
        self._commission_buffer = {}
        for i in range(self._retry_times):
            symbol_list = [symbol for symbol in symbol_list if make_underlying_symbol(symbol) not in self._commission_buffer]

            for symbol in symbol_list:
                if make_underlying_symbol(symbol) not in self._commission_buffer:
                    self.tdApi.qryCommission(symbol)
                    sleep(self._retry_interval * (i+1))

        event = Event(type_=EVENT_COMMISSION)
        event.dict_['data'] = self._commission_buffer
        self.eventEngine.put(event)


class RqCtpMdApi(CtpMdApi):

    def __init__(self, gateway):
        super(RqCtpMdApi, self).__init__(gateway)

    def onFrontConnected(self):
        super(RqCtpMdApi, self).onFrontConnected()
        system_log.info('CTP行情服务器连接成功')

    def onFrontDisconnected(self, n):
        super(RqCtpMdApi, self).onFrontDisconnected(n)
        system_log.info('CTP行服务器断开连接')

    def onRspError(self, error, n, last):
        system_log.error('CTP行情服务器错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspUserLogin(self, data, error, n, last):
        # 登录后会自动订阅之前已订阅了的合约
        super(RqCtpMdApi, self).onRspUserLogin(data, error, n, last)
        if error['ErrorID'] == 0:
            system_log.info('CTP行情服务器登录成功')
        else:
            system_log.error('CTP行情服务器登录错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspUserLogout(self, data, error, n, last):
        super(RqCtpMdApi, self).onRspUserLogout(data, error, n, last)
        if error['ErrorID'] == 0:
            system_log.info('CTP行情服务器登出成功')
        else:
            system_log.error('CTP行情服务器登出错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRtnDepthMarketData(self, data):

        tick = VtTickData()
        tick.gatewayName = self.gatewayName

        tick.symbol = data['InstrumentID']
        tick.exchange = exchangeMapReverse.get(data['ExchangeID'], u'未知')
        tick.vtSymbol = tick.symbol

        tick.lastPrice = data['LastPrice']
        tick.volume = data['Volume']
        tick.openInterest = data['OpenInterest']
        tick.time = '.'.join([data['UpdateTime'], str(data['UpdateMillisec'] / 100)])

        tick.date = datetime.now().strftime('%Y%m%d')

        tick.openPrice = data['OpenPrice']
        tick.highPrice = data['HighestPrice']
        tick.lowPrice = data['LowestPrice']
        tick.preClosePrice = data['PreClosePrice']

        tick.upperLimit = data['UpperLimitPrice']
        tick.lowerLimit = data['LowerLimitPrice']

        tick.bidPrice1 = data['BidPrice1']
        tick.bidVolume1 = data['BidVolume1']
        tick.askPrice1 = data['AskPrice1']
        tick.askVolume1 = data['AskVolume1']

        self.gateway.onTick(tick)

class RqCtpTdApi(CtpTdApi):

    def __init__(self, gateway):
        super(RqCtpTdApi, self).__init__(gateway)
        self.pos_buffer_dict = {}
        self.contract_buffer_dict = {}
        self.commission_buffer_dict = {}

    def onFrontConnected(self):
        super(RqCtpTdApi, self).onFrontConnected()
        system_log.info('CTP交易服务器连接成功')

    def onFrontDisconnected(self, n):
        super(RqCtpTdApi, self).onFrontDisconnected(n)
        system_log.info('CTP交易服务器断开连接')

    def onRspAuthenticate(self, data, error, n, last):
        super(RqCtpTdApi, self).onRspAuthenticate(data, error, n, last)
        if error['ErrorID'] == 0:
            system_log.info('CTP交易服务器验证成功')

    def onRspUserLogin(self, data, error, n, last):
        if error['ErrorID'] == 0:
            self.frontID = str(data['FrontID'])
            self.sessionID = str(data['SessionID'])
            self.loginStatus = True
            self.gateway.tdConnected = True
            system_log.info('CTP交易服务器登录成功')

        else:
            system_log.error('CTP交易服务器登录错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspUserLogout(self, data, error, n, last):
        super(RqCtpTdApi, self).onRspUserLogout(data, error, n, last)
        if error['ErrorID'] == 0:
            system_log.info('CTP交易服务器登出成功')
        else:
            system_log.error('CTP交易服务器登出错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspOrderInsert(self, data, error, n, last):
        system_log.error('CTP交易服务器发单错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspOrderAction(self, data, error, n, last):
        system_log.error('CTP交易服务器撤单错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onRspSettlementInfoConfirm(self, data, error, n, last):
        system_log.info('CTP交易服务器结算信息确认成功')
        self.gateway.onSettlementInfoConfirm()

    def onRspQryInvestorPosition(self, data, error, n, last):
        if not data['InstrumentID']:
            return

        posName = '.'.join([data['InstrumentID'], data['PosiDirection']])
        if posName in self.posDict:
            pos = self.posDict[posName]
        else:
            pos = RQPositionData()
            self.posDict[posName] = pos

            pos.gatewayName = self.gatewayName
            pos.symbol = data['InstrumentID']
            pos.vtSymbol = pos.symbol
            pos.direction = posiDirectionMapReverse.get(data['PosiDirection'], '')
            pos.vtPositionName = '.'.join([pos.vtSymbol, pos.direction])

        cost = pos.price * pos.position

        if data['YdPosition']:
            pos.ydPosition = data['Position']
        if data['TodayPosition']:
            pos.todayPosition = data['TodayPosition']

        pos.position += data['Position']
        pos.positionProfit += data['PositionProfit']

        if pos.position:
            pos.price = (cost + data['PositionCost']) / pos.position

        if pos.direction is DIRECTION_LONG:
            pos.frozen += data['LongFrozen']
        else:
            pos.frozen += data['ShortFrozen']

        pos.closeProfit += data['CloseProfit']
        pos.commission += data['Commission']
        pos.openCost += data['OpenCost']

        size = self.symbolSizeDict.get(data['InstrumentID'], 1)

        if pos.position > 0:
            pos.avgOpenPrice = pos.openCost / (pos.position * size)
        else:
            pos.avgOpenPrice = 0

        if data['PreSettlementPrice']:
            pos.preSettlementPrice = data['PreSettlementPrice']

        if last:
            self.gateway.onPosition(self.posDict.copy())

            self.posDict.clear()

    def onRspQryInstrument(self, data, error, n, last):
        if len(data['InstrumentID']) <= 7 and not make_underlying_symbol(data['InstrumentID']).endswith('EFP'):
            contract = RQContractData()
            contract.gatewayName = self.gatewayName

            contract.symbol = data['InstrumentID']
            contract.exchange = exchangeMapReverse[data['ExchangeID']]
            contract.vtSymbol = contract.symbol  # '.'.join([contract.symbol, contract.exchange])
            contract.name = data['InstrumentName'].decode('GBK')

            contract.size = data['VolumeMultiple']
            contract.priceTick = data['PriceTick']
            contract.strikePrice = data['StrikePrice']
            contract.underlyingSymbol = data['UnderlyingInstrID']

            contract.productClass = productClassMapReverse.get(data['ProductClass'], PRODUCT_UNKNOWN)

            if data['OptionsType'] == '1':
                contract.optionType = OPTION_CALL
            elif data['OptionsType'] == '2':
                contract.optionType = OPTION_PUT

            contract.expireDate = data['ExpireDate']
            contract.openDate = data['OpenDate']
            contract.longMarginRatio = data['LongMarginRatio']
            contract.shortMarginRatio = data['ShortMarginRatio']

            self.symbolExchangeDict[contract.symbol] = contract.exchange
            self.symbolSizeDict[contract.symbol] = contract.size

            self.contract_buffer_dict[contract.symbol] = contract

        if last:
            self.gateway.onContract(self.contract_buffer_dict)

    def onRspQryInstrumentCommissionRate(self, data, error, n, last):
        commissionData = RQCommissionData()
        commissionData.symbol = data['InstrumentID']

        commissionData.OpenRatioByMoney = data['OpenRatioByMoney']
        commissionData.OpenRatioByVolume = data['OpenRatioByVolume']
        commissionData.CloseRatioByMoney = data['CloseRatioByMoney']
        commissionData.CloseRatioByVolume = data['CloseRatioByVolume']
        commissionData.CloseTodayRatioByMoney = data['CloseTodayRatioByMoney']
        commissionData.CloseTodayRatioByVolume = data['CloseTodayRatioByVolume']

        self.gateway.onCommission(commissionData)

    def onRspError(self, error, n, last):
        system_log.error('CTP交易服务器错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onErrRtnOrderInsert(self, data, error):
        system_log.error('CTP交易服务器发单错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def onErrRtnOrderAction(self, data, error):
        system_log.error('CTP交易服务器撤单错误，错误代码：%s，错误信息：%s' % (str(error['ErrorID']), error['ErrorMsg'].decode('gbk')))

    def qrySettlementInfoConfirm(self):
        # 登录成功后应确认结算信息
        req = {}
        req['BrokerID'] = self.brokerID
        req['InvestorID'] = self.userID
        self.reqID += 1
        self.reqSettlementInfoConfirm(req, self.reqID)

    def qryInstrument(self):
        self.reqID += 1
        self.reqQryInstrument({}, self.reqID)

    def qryCommission(self, instrumentId):
        self.reqID += 1
        req = {
            'InstrumentID': instrumentId,
            'InvestorID': self.userID,
            'BrokerID': self.brokerID,
            'ExchangeID': self.symbolExchangeDict.get(instrumentId, EXCHANGE_UNKNOWN)
        }
        self.reqQryInstrumentCommissionRate(req, self.reqID)

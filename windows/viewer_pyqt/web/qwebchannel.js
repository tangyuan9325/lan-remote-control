"use strict";
var QWebChannelMessageTypes={signal:1,propertyUpdate:2,init:3,idle:4,debug:5,invokeMethod:6,connectToSignal:7,disconnectFromSignal:8,setProperty:9,response:10};
var QWebChannel=function(transport,initCallback){if(typeof transport!=="object"||typeof transport.send!=="function"){console.error("The QWebChannel expects a transport object with a send function and onmessage callback property.");return;}
var channel=this;this.transport=transport;this.send=function(data){if(typeof(data)!=="string"){data=JSON.stringify(data);}
channel.transport.send(data);}
this.transport.onmessage=function(message){var data=message.data;if(typeof data==="string"){data=JSON.parse(data);}
switch(data.type){case QWebChannelMessageTypes.signal:channel.handleSignal(data);break;case QWebChannelMessageTypes.response:channel.handleResponse(data);break;case QWebChannelMessageTypes.propertyUpdate:channel.handlePropertyUpdate(data);break;default:console.error("invalid message received:",message.data);break;}}
this.execCallbacks={};this.execId=0;this.exec=function(data,callback){if(!callback){channel.send(data);return;}
if(channel.execId===Number.MAX_VALUE){channel.execId=Number.MIN_VALUE;}
if(data.hasOwnProperty("id")){console.error("Cannot exec message with property id: "+JSON.stringify(data));return;}
data.id=channel.execId++;channel.execCallbacks[data.id]=callback;channel.send(data);};
this.objects={};this.handleSignal=function(message){var object=channel.objects[message.object];if(object){object.signalEmitted(message.signal,message.args);}else{console.warn("Unhandled signal: "+message.object+"::"+message.signal);}}
this.handleResponse=function(message){if(!message.hasOwnProperty("id")){console.error("Invalid response message received: ",JSON.stringify(message));return;}
channel.execCallbacks[message.id](message.data);delete channel.execCallbacks[message.id];}
this.handlePropertyUpdate=function(message){for(var i in message.data){var data=message.data[i];var object=channel.objects[data.object];if(object){object.propertyUpdate(data.signals,data.properties);}else{console.warn("Unhandled property update: "+data.object+"::"+data.signal);}}
channel.exec({type:QWebChannelMessageTypes.idle});}
this.debug=function(message){channel.send({type:QWebChannelMessageTypes.debug,data:message});};
channel.exec({type:QWebChannelMessageTypes.init},function(data){for(var objectName in data){var object=new QObject(objectName,data[objectName],channel);}
for(var objectName in channel.objects){channel.objects[objectName].unwrapProperties();}
if(initCallback){initCallback(channel);}
channel.exec({type:QWebChannelMessageTypes.idle});});};
function QObject(name,data,webChannel){this.__id__=name;webChannel.objects[name]=this;this.__objectSignals__={};this.__propertyCache__={};var object=this;this.unwrapQObject=function(response){if(response instanceof Array){var ret=new Array(response.length);for(var i=0;i<response.length;++i){ret[i]=object.unwrapQObject(response[i]);}
return ret;}
if(!response||!response["__QObject*__"]||response.id===undefined){return response;}
var objectId=response.id;if(webChannel.objects[objectId])
return webChannel.objects[objectId];if(!response.data){console.error("Cannot unwrap unknown QObject "+objectId+" without data.");return;}
var qObject=new QObject(objectId,response.data,webChannel);qObject.destroyed.connect(function(){if(webChannel.objects[objectId]===qObject){delete webChannel.objects[objectId];for(var func in qObject){if(typeof qObject[func]==="function"){qObject[func]=function(){};}}}});return qObject;};
this.unwrapProperties=function(){for(var propertyIdx in object.__propertyCache__){object.__propertyCache__[propertyIdx]=object.unwrapQObject(object.__propertyCache__[propertyIdx]);}};
function addSignal(signalData,isPropertyNotifySignal){var signalName=signalData[0];var signalIndex=signalData[1];object[signalName]={connect:function(callback){if(typeof(callback)!=="function"){console.error("Bad callback given to connect to signal "+signalName);return;}
object.__objectSignals__[signalIndex]=object.__objectSignals__[signalIndex]||[];object.__objectSignals__[signalIndex].push(callback);if(!isPropertyNotifySignal&&signalName!=="destroyed"){webChannel.exec({type:QWebChannelMessageTypes.connectToSignal,object:object.__id__,signal:signalIndex});}},disconnect:function(callback){if(typeof(callback)!=="function"){console.error("Bad callback given to disconnect from signal "+signalName);return;}
object.__objectSignals__[signalIndex]=object.__objectSignals__[signalIndex]||[];var idx=object.__objectSignals__[signalIndex].indexOf(callback);if(idx===-1){console.error("Cannot find connection of signal "+signalName+" to "+callback.name);return;}
object.__objectSignals__[signalIndex].splice(idx,1);if(!isPropertyNotifySignal&&object.__objectSignals__[signalIndex].length===0){webChannel.exec({type:QWebChannelMessageTypes.disconnectFromSignal,object:object.__id__,signal:signalIndex});}}};}
this.signalEmitted=function(signalName,signalArgs){var connections=object.__objectSignals__[signalName];if(connections){var unwrappedArgs=object.unwrapQObject(signalArgs);for(var i=0;i<connections.length;++i){connections[i].apply(object,unwrappedArgs);}}};
function addMethod(methodData){var methodName=methodData[0];var methodIdx=methodData[1];object[methodName]=function(){var args=[];var callback;for(var i=0;i<arguments.length;++i){var argument=arguments[i];if(typeof argument!=="function")
args.push(argument);else
callback=argument;}
webChannel.exec({"type":QWebChannelMessageTypes.invokeMethod,"object":object.__id__,"method":methodIdx,"args":args},function(response){if(response!==undefined){var result=object.unwrapQObject(response);if(callback){(callback)(result);}}});};}
function bindGetterSetter(propertyInfo){var propertyIndex=propertyInfo[0];var propertyName=propertyInfo[1];var notifySignalData=propertyInfo[2];object.__propertyCache__[propertyIndex]=propertyInfo[3];if(notifySignalData){if(notifySignalData[0]===1){addSignal([notifySignalData[1],notifySignalData[2]],true);}else{console.error("Invalid notify signal data for property "+propertyName+":"+JSON.stringify(propertyInfo));}}
Object.defineProperty(object,propertyName,{configurable:true,get:function(){return object.__propertyCache__[propertyIndex];},set:function(value){if(object.__propertyCache__[propertyIndex]!==value){object.__propertyCache__[propertyIndex]=value;webChannel.exec({"type":QWebChannelMessageTypes.setProperty,"object":object.__id__,"property":propertyIndex,"value":value});}}});}
this.propertyUpdate=function(signals,propertyMap){for(var propertyIndex in propertyMap){object.__propertyCache__[propertyIndex]=propertyMap[propertyIndex];}
for(var signalName in signals){var connections=object.__objectSignals__[signalName];if(connections){var args=signals[signalName];var unwrappedArgs=object.unwrapQObject(args);for(var i=0;i<connections.length;++i){connections[i].apply(object,unwrappedArgs);}}}};
for(var methodIdx in data.methods){var method=data.methods[methodIdx];addMethod(method);}
for(var signalIdx in data.signals){var signal=data.signals[signalIdx];addSignal(signal,false);}
for(var propertyIdx in data.properties){var property=data.properties[propertyIdx];bindGetterSetter(property);}
for(var enumIdx in data.enums){var enumObj=data.enums[enumIdx];object[enumObj.name]={};for(var key in enumObj.values){object[enumObj.name][key]=enumObj.values[key];}}}
